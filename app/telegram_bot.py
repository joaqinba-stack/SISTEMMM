"""Bot de Telegram: se manda la foto de una factura y se carga la compra."""
import difflib
import html
import logging
import secrets
import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from app import config, db, etiquetas_pdf, factura_ia, impresora, inventario as inv

log = logging.getLogger(__name__)

PENDIENTES: dict[str, dict] = {}  # compras leídas esperando que la persona confirme
VENCE_SEGUNDOS = 3600

AYUDA = (
    "👋 <b>¡Hola!</b> Soy el ayudante del inventario.\n\n"
    "📷 <b>Para cargar una compra:</b> mandame una <b>foto de la factura</b>. "
    "Yo la leo y te muestro lo que entendí. Si está bien, tocás <b>✅ Sí, guardar</b>.\n\n"
    "🔎 <b>Para ver el stock:</b> escribí <code>/stock</code> y el nombre, por ejemplo "
    "<code>/stock molde vela</code>\n\n"
    "💡 Consejo: sacá la foto de cerca, con buena luz y la factura derechita."
)


# ---------------------------------------------------------------- lógica (sin Telegram)

def preparar_compra(datos: dict, foto: str | None) -> dict:
    """Busca cada renglón de la factura entre los productos que ya existen."""
    with db.conexion() as con:
        clave = inv.normalizar(datos["proveedor"])
        fila = con.execute("SELECT id FROM proveedores WHERE clave = ?", (clave,)).fetchone()
        prov_id = fila[0] if fila else None
        repetida = bool(prov_id and inv.compra_existente(con, prov_id, datos["nro_factura"]))
        items = []
        for item in datos["items"]:
            producto = inv.buscar_coincidencia(con, item["descripcion"], prov_id)
            items.append({
                **item,
                "producto_id": producto["id"] if producto else None,
                "producto_nombre": producto["nombre_interno"] if producto else item["descripcion"],
            })
    return {**datos, "items": items, "foto": foto, "repetida": repetida}


def texto_resumen(compra: dict) -> str:
    e = html.escape
    lineas = ["🧾 <b>Esto es lo que leí de la factura:</b>", ""]
    lineas.append(f"🏪 Proveedor: <b>{e(compra['proveedor'] or 'no se lee')}</b>")
    lineas.append(f"🔢 Factura N°: <b>{e(compra['nro_factura'] or 'no se lee')}</b>")
    lineas.append(f"📅 Fecha: <b>{inv.fecha_txt(inv.leer_fecha(compra['fecha']))}</b>")
    lineas.append("")
    total = 0.0
    for n, item in enumerate(compra["items"], 1):
        marca = "✔️" if item["producto_id"] else "🆕"
        lineas.append(
            f"{n}. {marca} <b>{e(item['producto_nombre'])}</b>\n"
            f"     {inv.cantidad_txt(item['cantidad'])} × {inv.guaranies(item['precio_unitario'])}"
            f" = {inv.guaranies(item['total'])}"
        )
        total += item["total"]
    lineas.append("")
    lineas.append(f"💰 <b>Total: {inv.guaranies(total)}</b>")
    lineas.append("")
    lineas.append("✔️ = producto que ya existe   🆕 = producto nuevo")
    if compra["repetida"]:
        lineas.append("")
        lineas.append("⚠️ <b>Atención: esta factura ya fue cargada antes.</b>")
    lineas.append("")
    lineas.append("¿Guardo esta compra? Si algún producto está mal, tocá <b>✏️ Corregir</b> con su número.")
    return "\n".join(lineas)


def guardar_compra(compra: dict) -> tuple[str, int]:
    """Guarda la compra confirmada. Los productos nuevos se crean con códigos automáticos.
    Los renglones corregidos a mano enseñan su nombre para la próxima factura."""
    nuevos, lineas = 0, []
    with db.conexion() as con:
        items = []
        for item in compra["items"]:
            producto_id = item["producto_id"]
            if producto_id is None or not inv.obtener_producto(con, producto_id):
                creado = inv.crear_producto(con, {
                    "nombre_interno": item["descripcion"],
                    "nombre_proveedor": item["descripcion"],
                    "categoria": "Varios",
                    "proveedor": compra["proveedor"],
                    "costo_unitario": item["precio_unitario"],
                    "revisar": True,
                })
                producto_id = creado["id"]
                nuevos += 1
            elif item.get("aprender"):
                inv.agregar_alias(con, producto_id, item["descripcion"])
            items.append({"producto_id": producto_id, "cantidad": item["cantidad"],
                          "costo_total": item["total"]})
        compra_id = inv.registrar_compra(con, compra["fecha"], compra["nro_factura"], compra["proveedor"],
                                         items, foto=compra.get("foto"), origen="telegram")
        for item in items:
            p = inv.obtener_producto(con, item["producto_id"])
            lineas.append(f"• {html.escape(p['nombre_interno'])}: ahora hay "
                          f"<b>{inv.cantidad_txt(p['stock'])}</b>")
    texto = "✅ <b>¡Listo! Guardé la compra.</b>\n\n" + "\n".join(lineas)
    if nuevos:
        texto += (f"\n\n🆕 Se crearon {nuevos} producto(s) nuevo(s) con su código automático. "
                  "Quedaron marcados para revisar en la página (categoría y precio de venta).")
    return texto, compra_id


def opciones_correccion(texto: str, limite: int = 6) -> list:
    """Productos que se parecen a lo que escribió la persona para corregir un renglón."""
    q = inv.normalizar(texto)
    if not q:
        return []
    with db.conexion() as con:
        productos = inv.listar_productos(con)
    def puntaje(p):
        nombre = inv.normalizar(f"{p['nombre_interno']} {p['nombre_proveedor']}")
        contiene = all(palabra in f"{nombre} {inv.normalizar(p['codigo'])} {inv.normalizar(p['sku'])}"
                       for palabra in q.split())
        return (contiene, difflib.SequenceMatcher(None, q, inv.normalizar(p["nombre_interno"])).ratio())
    ordenados = sorted(productos, key=puntaje, reverse=True)
    return [p for p in ordenados if puntaje(p)[0] or puntaje(p)[1] >= 0.5][:limite]


def corregir_renglon(compra: dict, n: int, producto_id: int | None) -> dict:
    """Cambia a qué producto corresponde el renglón n (None = producto nuevo)."""
    item = compra["items"][n]
    if producto_id:
        with db.conexion() as con:
            producto = inv.obtener_producto(con, producto_id)
        if not producto:
            raise inv.ErrorNegocio("Ese producto ya no existe.")
        item.update(producto_id=producto["id"], producto_nombre=producto["nombre_interno"], aprender=True)
    else:
        item.update(producto_id=None, producto_nombre=item["descripcion"], aprender=False)
    return compra


def teclado_resumen(clave: str, compra: dict) -> InlineKeyboardMarkup:
    filas = [[InlineKeyboardButton("✅ Sí, guardar", callback_data=f"g:{clave}"),
              InlineKeyboardButton("❌ No, cancelar", callback_data=f"c:{clave}")]]
    correcciones = [InlineKeyboardButton(f"✏️ Corregir {n}", callback_data=f"k:{clave}:{n - 1}")
                    for n in range(1, len(compra["items"]) + 1)]
    filas += [correcciones[i:i + 3] for i in range(0, len(correcciones), 3)]
    return InlineKeyboardMarkup(filas)


def texto_stock(busqueda: str) -> str:
    busqueda = inv.normalizar(busqueda)
    with db.conexion() as con:
        filas = [p for p in inv.listar_productos(con, solo_activos=True)
                 if busqueda in inv.normalizar(f"{p['nombre_interno']} {p['codigo']} {p['sku']}")]
    if not filas:
        return "No encontré productos con ese nombre. 🤔"
    lineas = [f"📦 <b>{len(filas)} producto(s):</b>", ""]
    for p in filas[:25]:
        lineas.append(f"• <b>{html.escape(p['nombre_interno'])}</b>\n"
                      f"   Stock: <b>{inv.cantidad_txt(p['stock'])}</b> · "
                      f"Precio: {inv.guaranies(p['precio_venta'])}")
    if len(filas) > 25:
        lineas.append(f"\n… y {len(filas) - 25} más. Escribí un nombre más exacto.")
    return "\n".join(lineas)


def _limpiar_vencidos() -> None:
    ahora = time.time()
    for clave in [k for k, v in PENDIENTES.items() if v["vence"] < ahora]:
        PENDIENTES.pop(clave, None)


# ---------------------------------------------------------------- manejadores de Telegram

def _permitido(update: Update) -> bool:
    usuario = update.effective_user
    return bool(usuario and str(usuario.id) in config.TELEGRAM_USUARIOS_PERMITIDOS)


async def _rechazar(update: Update) -> None:
    await update.effective_message.reply_text(
        "🔒 Todavía no tenés permiso para usar este bot.\n"
        f"Tu número de usuario es: {update.effective_user.id}\n"
        "Pasáselo a quien administra el sistema para que te habilite."
    )


async def cmd_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _permitido(update):
        return await _rechazar(update)
    await update.effective_message.reply_html(AYUDA)


async def cmd_mi_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"Tu número de usuario es: {update.effective_user.id}")


async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _permitido(update):
        return await _rechazar(update)
    busqueda = " ".join(context.args or [])
    if not busqueda:
        return await update.effective_message.reply_html(
            "Escribí el nombre después de /stock, por ejemplo: <code>/stock balanza</code>")
    await update.effective_message.reply_html(texto_stock(busqueda))


async def recibir_factura(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _permitido(update):
        return await _rechazar(update)
    mensaje = update.effective_message
    if mensaje.photo:
        archivo = await mensaje.photo[-1].get_file()
        media_type, extension = "image/jpeg", ".jpg"
    else:
        doc = mensaje.document
        media_type = doc.mime_type or "image/jpeg"
        extension = ".pdf" if media_type == "application/pdf" else ".jpg"
        archivo = await doc.get_file()

    aviso = await mensaje.reply_text("📷 Recibí la foto. Estoy leyendo la factura, esperá un ratito…")
    contenido = bytes(await archivo.download_as_bytearray())
    nombre_foto = f"factura-{uuid.uuid4().hex[:12]}{extension}"
    carpeta = config.ARCHIVOS_DIR / "facturas"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / nombre_foto).write_bytes(contenido)

    try:
        datos = await factura_ia.extraer(contenido, media_type)
    except factura_ia.ErrorLectura as exc:
        await aviso.edit_text(f"😕 {exc}")
        return
    if not datos["es_factura"] or not datos["items"]:
        await aviso.edit_text(
            "😕 No encontré productos en esa foto.\n"
            "Probá sacar la foto más de cerca, con buena luz y que se vea toda la factura.")
        return

    compra = preparar_compra(datos, f"facturas/{nombre_foto}")
    _limpiar_vencidos()
    clave = secrets.token_hex(4)
    PENDIENTES[clave] = {**compra, "vence": time.time() + VENCE_SEGUNDOS}
    await aviso.edit_text(texto_resumen(compra), parse_mode="HTML", reply_markup=teclado_resumen(clave, compra))


async def responder_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    if not _permitido(update):
        return
    partes = (consulta.data or "").split(":")
    accion = partes[0]

    if accion == "e":  # imprimir las etiquetas de una factura ya guardada
        try:
            with db.conexion() as con:
                lista, _ = etiquetas_pdf.etiquetas_de_factura(con, int(partes[1]))
                tamano = etiquetas_pdf.tamano_elegido(con)
            n = impresora.imprimir(lista, tamano)
            await consulta.message.reply_text(f"🖨️ Listo, mandé {n} etiqueta(s) a la impresora.")
        except (inv.ErrorNegocio, impresora.ErrorImpresora) as exc:
            await consulta.message.reply_text(f"😕 No pude imprimir: {exc}")
        return

    clave = partes[1] if len(partes) > 1 else ""
    if accion in ("k", "p"):
        compra = PENDIENTES.get(clave)
        if compra is None:
            await consulta.edit_message_text("⌛ Esta factura ya fue guardada, cancelada o pasó mucho tiempo.")
            return
        n = int(partes[2])
        if accion == "k":  # pedir el nombre del producto correcto
            context.user_data["corrigiendo"] = (clave, n)
            item = compra["items"][n]
            await consulta.message.reply_html(
                f"✏️ <b>Producto {n + 1}:</b> «{html.escape(item['descripcion'])}»\n\n"
                "Escribí parte del nombre del producto correcto (por ejemplo: <i>molde vela</i>).",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    "🆕 Es un producto nuevo", callback_data=f"p:{clave}:{n}:0")]]))
            return
        context.user_data.pop("corrigiendo", None)
        try:
            corregir_renglon(compra, n, int(partes[3]) or None)
        except inv.ErrorNegocio as exc:
            await consulta.edit_message_text(f"😕 {exc}")
            return
        await consulta.edit_message_text(texto_resumen(compra), parse_mode="HTML",
                                         reply_markup=teclado_resumen(clave, compra))
        return

    compra = PENDIENTES.pop(clave, None)
    if compra is None:
        await consulta.edit_message_text(
            "⌛ Esta factura ya fue guardada, cancelada o pasó mucho tiempo. "
            "Si hace falta, mandá la foto de nuevo.")
        return
    if accion == "c":
        await consulta.edit_message_text("❌ Cancelado. No guardé nada.")
        return
    teclado = None
    try:
        texto, compra_id = guardar_compra(compra)
        if impresora.configurada():
            teclado = InlineKeyboardMarkup([[InlineKeyboardButton(
                "🏷️ Imprimir etiquetas de esta factura", callback_data=f"e:{compra_id}")]])
        else:
            texto += "\n\n🏷️ Las etiquetas de esta factura están en la página, en Imprimir códigos de barra."
    except inv.ErrorNegocio as exc:
        texto = f"😕 No pude guardar: {exc}"
    except Exception:
        log.exception("Error guardando compra de Telegram")
        texto = "😕 Hubo un problema y no se guardó. Probá de nuevo más tarde."
    if config.URL_PUBLICA:
        texto += f"\n\n🔗 Ver compras: {config.URL_PUBLICA}/compras"
    await consulta.edit_message_text(texto, parse_mode="HTML", reply_markup=teclado)


async def texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _permitido(update):
        return await _rechazar(update)
    corrigiendo = context.user_data.get("corrigiendo") if context.user_data is not None else None
    if corrigiendo and corrigiendo[0] in PENDIENTES:
        clave, n = corrigiendo
        opciones = opciones_correccion(update.effective_message.text or "")
        filas = [[InlineKeyboardButton(f"{p['nombre_interno'][:40]} ({p['codigo']})",
                                       callback_data=f"p:{clave}:{n}:{p['id']}")] for p in opciones]
        filas.append([InlineKeyboardButton("🆕 Es un producto nuevo", callback_data=f"p:{clave}:{n}:0")])
        texto = ("Tocá el producto correcto:" if opciones else
                 "No encontré productos con ese nombre. Probá con otra palabra, o tocá «Es un producto nuevo».")
        await update.effective_message.reply_text(texto, reply_markup=InlineKeyboardMarkup(filas))
        return
    await update.effective_message.reply_html(AYUDA)


def crear_aplicacion() -> Application:
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler(["start", "ayuda", "help"], cmd_inicio))
    app.add_handler(CommandHandler("miid", cmd_mi_id))
    app.add_handler(CommandHandler("stock", cmd_stock))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF, recibir_factura))
    app.add_handler(CallbackQueryHandler(responder_boton))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto_libre))
    return app

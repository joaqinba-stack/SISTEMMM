"""Bot de Telegram: se manda la foto de una factura y se carga la compra."""
import difflib
import html
import logging
import re
import secrets
import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from app import config, db, etiquetas_pdf, factura_ia, impresora, inventario as inv, usuarios

log = logging.getLogger(__name__)

PENDIENTES: dict[str, dict] = {}  # compras leídas esperando que la persona confirme
VENCE_SEGUNDOS = 3600

AYUDA = (
    "👋 <b>¡Hola!</b> Soy el ayudante del inventario.\n\n"
    "📷 <b>Para cargar una compra:</b> mandame una <b>foto de la factura</b>. "
    "Yo la leo y te muestro lo que entendí. Si está bien, tocás <b>✅ Sí, guardar</b>.\n\n"
    "🔎 <b>Para ver el stock:</b> escribí <code>/stock</code> y el nombre, por ejemplo "
    "<code>/stock molde vela</code>\n\n"
    "💡 Consejo: sacá la foto de cerca, con buena luz y la factura derechita.\n"
    "📏 Si la factura es <b>muy larga</b>, mandala como <b>archivo</b> (📎 → Archivo) y no como "
    "foto: así Telegram no la achica y se leen todos los productos."
)


# ---------------------------------------------------------------- lógica (sin Telegram)

TOLERANCIA_TOTAL = 100  # guaraníes de diferencia que se aceptan por redondeos
MAX_BOTONES = 100       # tope de Telegram para un teclado
MAX_TEXTO = 3800        # Telegram no acepta mensajes de más de 4096 letras: los largos van en partes
MAX_CORREGIR = 15       # con más renglones, un solo botón «Corregir un producto» en vez de uno por renglón
MAX_SUGERENCIAS = 12    # botones «🔁 n es: …» como máximo, para que el teclado no sea una pared

AVISO_FOTO_ACHICADA = ("Esta factura es larga y Telegram achicó la foto, así que puede que no se lean "
                       "todos los productos. Mandala como archivo (📎 → Archivo) para que llegue "
                       "con buena calidad.")


def partir(lineas: list[str], limite: int = MAX_TEXTO) -> list[str]:
    """Junta las líneas en mensajes de hasta `limite` letras, sin cortar ninguna línea por la mitad
    (cada línea tiene sus etiquetas HTML completas)."""
    partes, actual = [], ""
    for linea in lineas:
        linea = linea[:limite]
        if actual and len(actual) + 1 + len(linea) > limite:
            partes.append(actual)
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual or not partes:
        partes.append(actual)
    return partes


def verificar(datos: dict) -> tuple[dict, list[str]]:
    """Controles que no dependen de la IA: el CDC y la suma de los renglones.
    Devuelve los datos (con número y fecha del CDC si es válido) y los avisos para la persona."""
    avisos = []
    cdc = factura_ia.leer_cdc(datos.get("cdc", ""))
    if cdc:
        if re.sub(r"\D", "", datos["nro_factura"]) != re.sub(r"\D", "", cdc["nro_factura"]):
            avisos.append(f"Leí el número de factura como «{datos['nro_factura'] or 'vacío'}», "
                          f"pero según el CDC es {cdc['nro_factura']}. Uso el del CDC.")
        datos = {**datos, "nro_factura": cdc["nro_factura"], "fecha": cdc["fecha"]}
    suma = sum(item["total"] for item in datos["items"])
    total = datos.get("total_factura") or 0
    if total and suma < total - TOLERANCIA_TOTAL:
        avisos.append(f"Puede que falten productos: los renglones suman {inv.guaranies(suma)} pero el "
                      f"total de la factura es {inv.guaranies(total)} (faltan {inv.guaranies(total - suma)}). "
                      "Revisalo antes de guardar.")
    elif total and suma > total + TOLERANCIA_TOTAL:
        avisos.append(f"Los renglones suman {inv.guaranies(suma)} pero el total de la factura es "
                      f"{inv.guaranies(total)} (sobran {inv.guaranies(suma - total)}). "
                      "Revisá que no haya productos repetidos o mal leídos.")
    return {**datos, "cdc_ok": bool(cdc)}, avisos


def preparar_compra(datos: dict, foto: str | None, avisos_extra: list[str] | None = None) -> dict:
    """Busca cada renglón de la factura entre los productos que ya existen."""
    datos, avisos = verificar(datos)
    avisos = list(avisos_extra or []) + avisos
    with db.conexion() as con:
        clave = inv.normalizar(datos["proveedor"])
        fila = con.execute("SELECT id FROM proveedores WHERE clave = ?", (clave,)).fetchone()
        prov_id = fila[0] if fila else None
        repetida = bool(prov_id and inv.compra_existente(con, prov_id, datos["nro_factura"]))
        por_codigo = {f["codigo"]: f for f in con.execute("SELECT * FROM productos").fetchall()}
        items = []
        for item in datos["items"]:
            # 1) lo que reconoció la IA mirando la lista de productos; 2) nombres parecidos o aprendidos
            producto = por_codigo.get(item.get("codigo_existente", ""))
            aprender = bool(producto) and inv.normalizar(item["descripcion"]) not in (
                inv.normalizar(producto["nombre_interno"]), inv.normalizar(producto["nombre_proveedor"]))
            producto = producto or inv.buscar_coincidencia(con, item["descripcion"])
            # 3) si queda como nuevo, los que se le parecen se ofrecen para elegir con un toque
            sugerencias = [] if producto else [
                {"id": p["id"], "codigo": p["codigo"], "nombre": p["nombre_interno"]}
                for p in inv.parecidos(con, item["descripcion"])]
            items.append({
                **item,
                "producto_id": producto["id"] if producto else None,
                "producto_nombre": producto["nombre_interno"] if producto else item["descripcion"],
                "aprender": aprender,
                "sugerencias": sugerencias,
            })
    return {**datos, "items": items, "foto": foto, "repetida": repetida, "avisos": avisos}


def lineas_resumen(compra: dict) -> list[str]:
    e = html.escape
    lineas = ["🧾 <b>Esto es lo que leí de la factura:</b>", ""]
    lineas.append(f"🏪 Proveedor: <b>{e(compra['proveedor'] or 'no se lee')}</b>")
    lineas.append(f"🔢 Factura N°: <b>{e(compra['nro_factura'] or 'no se lee')}</b>")
    lineas.append(f"📅 Fecha: <b>{inv.fecha_txt(inv.leer_fecha(compra['fecha']))}</b>")
    lineas.append(f"📦 Productos: <b>{len(compra['items'])}</b>")
    if compra.get("cdc_ok"):
        lineas.append("🔐 Número y fecha confirmados con el CDC")
    if compra.get("avisos"):
        lineas.append(f"⚠️ <b>Hay {len(compra['avisos'])} aviso(s) al final: leelos antes de guardar.</b>")
    lineas.append("")
    total = 0.0
    hay_sugerencias = False
    for n, item in enumerate(compra["items"], 1):
        marca = "✔️" if item["producto_id"] else "🆕"
        renglon = (f"{n}. {marca} <b>{e(item['producto_nombre'])}</b>\n"
                   f"     {inv.cantidad_txt(item['cantidad'])} × {inv.guaranies(item['precio_unitario'])}"
                   f" = {inv.guaranies(item['total'])}")
        if item["producto_id"]:
            # Con el texto de la factura al lado se nota si se eligió el producto equivocado.
            if inv.normalizar(item["descripcion"]) != inv.normalizar(item["producto_nombre"]):
                renglon += f"\n     📄 En la factura: «{e(item['descripcion'])}»"
        elif item.get("sugerencias"):
            hay_sugerencias = True
            nombres = " · ".join(e(s["nombre"]) for s in item["sugerencias"])
            renglon += f"\n     🔎 Se parece a: <i>{nombres}</i>"
        lineas.append(renglon)  # un renglón entero va siempre en el mismo mensaje
        total += item["total"]
    lineas.append("")
    lineas.append(f"💰 <b>Total: {inv.guaranies(total)}</b>")
    for aviso in compra.get("avisos", []):
        lineas.append("")
        lineas.append(f"⚠️ <b>{e(aviso)}</b>")
    lineas.append("")
    lineas.append("✔️ = producto que ya existe   🆕 = producto nuevo")
    if hay_sugerencias:
        lineas.append("🔁 = tocá ese botón si el producto nuevo en realidad es ese")
    if compra["repetida"]:
        lineas.append("")
        lineas.append("⚠️ <b>Atención: esta factura ya fue cargada antes.</b>")
    lineas.append("")
    if len(compra["items"]) > MAX_CORREGIR:
        lineas.append("¿Guardo esta compra? Si algún producto está mal, tocá <b>✏️ Corregir un producto</b> "
                      "y escribí su número.")
    else:
        lineas.append("¿Guardo esta compra? Si algún producto está mal, tocá <b>✏️ Corregir</b> con su número.")
    return lineas


def texto_resumen(compra: dict) -> str:
    return "\n".join(lineas_resumen(compra))


def partes_resumen(compra: dict) -> list[str]:
    """El resumen en uno o varios mensajes (Telegram no acepta mensajes muy largos)."""
    partes = partir(lineas_resumen(compra))
    if len(partes) > 1:
        partes = [f"📄 <i>Parte {i} de {len(partes)}</i>\n{p}" for i, p in enumerate(partes, 1)]
    return partes


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
        contiene = all(palabra in f"{nombre} {inv.normalizar(p['codigo'])}"
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
    if len(compra["items"]) > MAX_CORREGIR:  # factura larga: se escribe el número del renglón
        correcciones = [InlineKeyboardButton("✏️ Corregir un producto", callback_data=f"n:{clave}")]
    else:
        correcciones = [InlineKeyboardButton(f"✏️ Corregir {n}", callback_data=f"k:{clave}:{n - 1}")
                        for n in range(1, len(compra["items"]) + 1)]
    libres = min(MAX_BOTONES - 2 - len(correcciones), MAX_SUGERENCIAS)
    for n, item in enumerate(compra["items"]):
        if item["producto_id"] is not None:
            continue
        for s in item.get("sugerencias", [])[:max(libres, 0)]:
            filas.append([InlineKeyboardButton(f"🔁 {n + 1} es: {s['nombre'][:30]} ({s['codigo']})",
                                               callback_data=f"p:{clave}:{n}:{s['id']}")])
            libres -= 1
    filas += [correcciones[i:i + 3] for i in range(0, len(correcciones), 3)]
    return InlineKeyboardMarkup(filas)


def texto_stock(busqueda: str) -> str:
    busqueda = inv.normalizar(busqueda)
    with db.conexion() as con:
        filas = [p for p in inv.listar_productos(con, solo_activos=True)
                 if busqueda in inv.normalizar(f"{p['nombre_interno']} {p['codigo']}")]
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


async def mostrar_resumen(clave: str, compra: dict, editar, enviar, boton_id=None) -> None:
    """Muestra el resumen de la compra. editar: corrutina que cambia el mensaje desde donde se
    viene (el «Estoy leyendo…» o el del botón tocado); enviar: corrutina que manda uno nuevo.
    boton_id: el mensaje del botón tocado, cuando se muestra de nuevo después de corregir.
    Si entra en un mensaje, es ese y lleva los botones. Si es largo va en partes, con los botones
    en la última; al mostrarlo de nuevo se borran las partes viejas y se manda entero otra vez."""
    partes = partes_resumen(compra)
    teclado = teclado_resumen(clave, compra)
    for mensaje in compra.get("mensajes", []):
        if getattr(mensaje, "message_id", None) == boton_id:
            continue  # ese se cambia, no se borra
        try:
            await mensaje.delete()
        except Exception:  # ya borrado o muy viejo: no importa
            pass
    compra["mensajes"] = []
    if len(partes) == 1:
        await editar(partes[0], parse_mode="HTML", reply_markup=teclado)
        return
    if boton_id is not None:  # el mensaje del botón queda como nota y el resumen va entero abajo
        await editar("✅ Listo, actualicé el resumen. Lo tenés acá abajo 👇")
        pendientes = partes
    else:
        compra["mensajes"].append(await editar(partes[0], parse_mode="HTML"))
        pendientes = partes[1:]
    for i, parte in enumerate(pendientes):
        ultima = i == len(pendientes) - 1
        compra["mensajes"].append(
            await enviar(parte, parse_mode="HTML", reply_markup=teclado if ultima else None))


async def responder_largo(texto: str, editar, enviar, teclado=None) -> None:
    """Un mensaje que puede pasar el largo de Telegram: el primer pedazo cambia el mensaje
    actual y el resto se manda aparte; los botones van en el último."""
    partes = partir(texto.split("\n"))
    for i, parte in enumerate(partes):
        ultima = i == len(partes) - 1
        accion = editar if i == 0 else enviar
        await accion(parte, parse_mode="HTML", reply_markup=teclado if ultima else None)


def _limpiar_vencidos() -> None:
    ahora = time.time()
    for clave in [k for k, v in PENDIENTES.items() if v["vence"] < ahora]:
        PENDIENTES.pop(clave, None)


# ---------------------------------------------------------------- manejadores de Telegram

def _permitido(update: Update) -> bool:
    # Sin lista de permitidos, el bot es de uso libre.
    if not config.TELEGRAM_USUARIOS_PERMITIDOS:
        return True
    usuario = update.effective_user
    return bool(usuario and str(usuario.id) in config.TELEGRAM_USUARIOS_PERMITIDOS)


def _anotar(update: Update, accion: str, detalle: str = "") -> None:
    """Deja en el registro de actividad lo que se hizo desde Telegram, con el nombre de quien fue."""
    persona = update.effective_user
    nombre = getattr(persona, "full_name", "") or str(getattr(persona, "id", ""))
    with db.conexion() as con:
        usuarios.registrar(con, None, accion, detalle, nombre=f"Telegram: {nombre}")


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
    try:
        await _leer_y_mostrar(mensaje, archivo, media_type, extension, aviso)
    except Exception:  # que nunca quede en «Estoy leyendo…» sin respuesta
        log.exception("Error leyendo una factura de Telegram")
        try:
            await aviso.edit_text(
                "😕 Hubo un problema y no pude mostrarte lo que leí. No guardé nada.\n"
                "Probá mandar la factura de nuevo. Si es muy larga, mandala como archivo (📎 → Archivo).")
        except Exception:
            log.exception("Tampoco se pudo avisar del error")


async def _leer_y_mostrar(mensaje, archivo, media_type: str, extension: str, aviso) -> None:
    contenido = bytes(await archivo.download_as_bytearray())
    nombre_foto = f"factura-{uuid.uuid4().hex[:12]}{extension}"
    carpeta = config.ARCHIVOS_DIR / "facturas"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / nombre_foto).write_bytes(contenido)
    # Una foto (no un archivo) de una factura larga llega achicada por Telegram.
    achicada = bool(mensaje.photo) and factura_ia.es_tira_larga(contenido)

    with db.conexion() as con:
        catalogo = inv.catalogo_para_ia(con)
    try:
        datos = await factura_ia.extraer(contenido, media_type, catalogo)
    except factura_ia.ErrorLectura as exc:
        await aviso.edit_text(f"😕 {exc}")
        return
    if not datos["es_factura"] or not datos["items"]:
        consejo = ("Esta factura es larga y Telegram achicó la foto: mandala como archivo (📎 → Archivo)."
                   if achicada else
                   "Probá sacar la foto más de cerca, con buena luz y que se vea toda la factura.")
        await aviso.edit_text(f"😕 No encontré productos en esa foto.\n{consejo}")
        return

    compra = preparar_compra(datos, f"facturas/{nombre_foto}", [AVISO_FOTO_ACHICADA] if achicada else None)
    _limpiar_vencidos()
    clave = secrets.token_hex(4)
    PENDIENTES[clave] = {**compra, "vence": time.time() + VENCE_SEGUNDOS}
    await mostrar_resumen(clave, PENDIENTES[clave], aviso.edit_text, mensaje.reply_text)


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
            _anotar(update, "Imprimió etiquetas", f"{n} etiqueta(s) de una factura")
            await consulta.message.reply_text(f"🖨️ Listo, mandé {n} etiqueta(s) a la impresora.")
        except (inv.ErrorNegocio, impresora.ErrorImpresora) as exc:
            await consulta.message.reply_text(f"😕 No pude imprimir: {exc}")
        return

    clave = partes[1] if len(partes) > 1 else ""
    if accion in ("k", "p", "n"):
        compra = PENDIENTES.get(clave)
        if compra is None:
            await consulta.edit_message_text("⌛ Esta factura ya fue guardada, cancelada o pasó mucho tiempo.")
            return
        if accion == "n":  # factura larga: se escribe el número del renglón a corregir
            context.user_data.pop("corrigiendo", None)
            context.user_data["eligiendo"] = clave
            await consulta.message.reply_html(
                f"✏️ Escribí el <b>número</b> del producto que querés corregir (del 1 al {len(compra['items'])}).")
            return
        n = int(partes[2])
        if accion == "k":  # pedir el nombre del producto correcto
            await _pedir_producto_correcto(consulta.message, context, clave, compra, n)
            return
        context.user_data.pop("corrigiendo", None)
        try:
            corregir_renglon(compra, n, int(partes[3]) or None)
        except inv.ErrorNegocio as exc:
            await consulta.edit_message_text(f"😕 {exc}")
            return
        await mostrar_resumen(clave, compra, consulta.edit_message_text, consulta.message.reply_text,
                              boton_id=getattr(consulta.message, "message_id", None))
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
        _anotar(update, "Cargó una compra", f"Factura {compra['nro_factura'] or 's/n'} de "
                f"{compra['proveedor'] or 'sin proveedor'}: {len(compra['items'])} renglón(es)")
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
    await responder_largo(texto, consulta.edit_message_text, consulta.message.reply_text, teclado)


async def _pedir_producto_correcto(mensaje, context, clave: str, compra: dict, n: int) -> None:
    context.user_data["corrigiendo"] = (clave, n)
    item = compra["items"][n]
    await mensaje.reply_html(
        f"✏️ <b>Producto {n + 1}:</b> «{html.escape(item['descripcion'])}»\n\n"
        "Escribí parte del nombre del producto correcto (por ejemplo: <i>molde vela</i>).",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            "🆕 Es un producto nuevo", callback_data=f"p:{clave}:{n}:0")]]))


async def texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _permitido(update):
        return await _rechazar(update)
    eligiendo = context.user_data.pop("eligiendo", None) if context.user_data is not None else None
    if eligiendo and eligiendo in PENDIENTES:
        compra = PENDIENTES[eligiendo]
        texto = (update.effective_message.text or "").strip()
        if texto.isdigit() and 1 <= int(texto) <= len(compra["items"]):
            return await _pedir_producto_correcto(update.effective_message, context, eligiendo,
                                                  compra, int(texto) - 1)
        if texto.isdigit():  # número fuera de la lista: sigue esperando uno válido
            context.user_data["eligiendo"] = eligiendo
            return await update.effective_message.reply_text(
                f"Ese número no está. Escribí un número del 1 al {len(compra['items'])}.")
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

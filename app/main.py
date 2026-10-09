"""Aplicación web del inventario (FastAPI)."""
import io
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from functools import lru_cache

import barcode
from barcode.writer import SVGWriter
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from starlette.middleware.sessions import SessionMiddleware

from app import codigos, config, db, etiquetas_pdf, exportar, graficos, impresora, inventario as inv
from app.etiqueta_diseno import TAMANOS

log = logging.getLogger(__name__)
CARPETA = config.RAIZ / "app"
MEDIOS_PAGO = ["Efectivo", "Transferencia", "Tarjeta", "Otro"]


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    db.inicializar()
    bot = None
    if config.TELEGRAM_TOKEN:
        from app.telegram_bot import crear_aplicacion
        try:
            bot = crear_aplicacion()
            await bot.initialize()
            await bot.start()
            await bot.updater.start_polling(drop_pending_updates=False)
            log.info("Bot de Telegram iniciado")
        except Exception:
            log.exception("No se pudo iniciar el bot de Telegram (revisá TELEGRAM_TOKEN)")
            bot = None
    else:
        log.warning("TELEGRAM_TOKEN vacío: el bot de Telegram no se inicia")
    yield
    if bot:
        await bot.updater.stop()
        await bot.stop()
        await bot.shutdown()


app = FastAPI(title="Inventario", lifespan=ciclo_de_vida)
app.mount("/static", StaticFiles(directory=CARPETA / "static"), name="static")
plantillas = Jinja2Templates(directory=CARPETA / "templates")
plantillas.env.filters["gs"] = inv.guaranies
plantillas.env.filters["cant"] = inv.cantidad_txt
plantillas.env.filters["fecha"] = inv.fecha_txt


@app.middleware("http")
async def exigir_clave(request: Request, siguiente):
    libre = request.url.path.startswith(("/static", "/login"))
    if config.CLAVE_ACCESO and not libre and not request.session.get("ok"):
        return RedirectResponse("/login", status_code=303)
    return await siguiente(request)


# Se agrega después para que corra antes que exigir_clave (y la sesión ya exista).
app.add_middleware(SessionMiddleware, secret_key=config.clave_secreta(),
                   max_age=60 * 60 * 24 * 90, same_site="lax")


# ---------------------------------------------------------------- utilidades

def avisar(request: Request, texto: str, tipo: str = "ok") -> None:
    request.session.setdefault("avisos", []).append([tipo, texto])


def pagina(request: Request, nombre: str, **contexto) -> HTMLResponse:
    avisos = request.session.pop("avisos", [])
    with db.conexion() as con:
        pendientes = inv.notificaciones(con)
    cantidad = len({p["id"] for lista in pendientes.values() for p in lista})
    return plantillas.TemplateResponse(
        request, nombre,
        {"avisos": avisos, "con_clave": bool(config.CLAVE_ACCESO),
         "cantidad_avisos": cantidad, **contexto},
    )


def json_para_html(datos) -> str:
    """JSON seguro para poner dentro de <script type="application/json">."""
    return json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")


def opciones_productos(productos) -> str:
    return json_para_html([
        {"id": p["id"], "texto": p["nombre_interno"],
         "detalle": f"{p['codigo']} · {inv.guaranies(p['precio_venta'])} · hay {inv.cantidad_txt(p['stock'])}",
         "buscar": f"{p['codigo']} {p['codigo_barra']}",
         "precio": p["precio_venta"], "stock": p["stock"],
         "imagen": f"/archivo/{p['imagen']}" if p["imagen"] else ""}
        for p in productos])


def ir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


async def guardar_imagen(archivo: UploadFile | None, carpeta: str) -> str | None:
    if archivo is None or not archivo.filename:
        return None
    contenido = await archivo.read()
    if not contenido:
        return None
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(contenido))).convert("RGB")
    except Exception:
        return None
    img.thumbnail((900, 900))
    destino = config.ARCHIVOS_DIR / carpeta
    destino.mkdir(parents=True, exist_ok=True)
    nombre = f"{uuid.uuid4().hex[:16]}.jpg"
    img.save(destino / nombre, format="JPEG", quality=85)
    return f"{carpeta}/{nombre}"


def excel(contenido: bytes, nombre: str) -> Response:
    return Response(
        contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}-{date.today()}.xlsx"'},
    )


# ---------------------------------------------------------------- acceso

@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return pagina(request, "login.html")


@app.post("/login")
def login_enviar(request: Request, clave: str = Form("")):
    if not config.CLAVE_ACCESO or clave.strip() == config.CLAVE_ACCESO:
        request.session["ok"] = True
        return ir("/")
    avisar(request, "La clave no es correcta. Probá de nuevo.", "error")
    return ir("/login")


@app.get("/salir")
def salir(request: Request):
    request.session.clear()
    return ir("/login")


# ---------------------------------------------------------------- notificaciones

@app.get("/", response_class=HTMLResponse)
def notificaciones(request: Request):
    with db.conexion() as con:
        pendientes = inv.notificaciones(con)
    return pagina(request, "notificaciones.html", stock_bajo=config.STOCK_BAJO, **pendientes)


# ---------------------------------------------------------------- productos

@app.get("/productos/nuevo", response_class=HTMLResponse)
def producto_nuevo(request: Request):
    with db.conexion() as con:
        return pagina(request, "producto_nuevo.html",
                      categorias=inv.categorias(con), proveedores=inv.proveedores(con),
                      proximo_codigo=codigos.nuevo_codigo_producto(con),
                      hoy=date.today().isoformat(), margen=config.MARGEN_POR_DEFECTO)


@app.post("/productos/nuevo")
async def producto_nuevo_guardar(
    request: Request,
    nombre_interno: str = Form(""), marca: str = Form(""), categoria_id: int = Form(...),
    proveedor: str = Form(""), nombre_proveedor: str = Form(""), fecha: str = Form(""),
    nro_factura: str = Form(""), cantidad: str = Form("0"), costo_total: str = Form("0"),
    margen_pct: str = Form(""), precio_venta: str = Form(""),
    imagen: UploadFile | None = File(None),
):
    ruta_imagen = await guardar_imagen(imagen, "productos")
    try:
        with db.conexion() as con:
            creado = inv.crear_producto(con, {
                "nombre_interno": nombre_interno, "marca": marca, "categoria_id": categoria_id,
                "proveedor": proveedor, "nombre_proveedor": nombre_proveedor, "fecha": fecha,
                "nro_factura": nro_factura, "cantidad": cantidad, "costo_total": costo_total,
                "margen_pct": margen_pct, "precio_venta": precio_venta, "imagen": ruta_imagen,
            })
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/productos/nuevo")
    avisar(request, f"✅ Producto guardado. Su código es {creado['codigo']}.")
    return ir(f"/productos#p{creado['id']}")


@app.get("/productos", response_class=HTMLResponse)
def productos(request: Request):
    with db.conexion() as con:
        return pagina(request, "productos.html", productos=inv.listar_productos(con),
                      categorias=inv.categorias(con), margen=config.MARGEN_POR_DEFECTO)


@app.get("/productos/excel")
def productos_excel():
    with db.conexion() as con:
        return excel(exportar.productos(con), "productos")


@app.post("/productos/{producto_id}/margen")
@app.post("/productos/{producto_id}/precio")
def producto_precio(request: Request, producto_id: int, margen_pct: str = Form(""),
                    precio_venta: str = Form(""), origen: str = Form("")):
    """Precio por % de ganancia o escrito a mano: las dos formas valen y se calculan entre sí."""
    try:
        with db.conexion() as con:
            actual = inv.obtener_producto(con, producto_id)
            if not actual:
                raise inv.ErrorNegocio("Ese producto no existe.")
            precio_escrito = inv.leer_numero(precio_venta)
            por_precio = origen == "precio" or (
                origen != "margen" and precio_escrito is not None
                and int(round(precio_escrito)) != actual["precio_venta"])
            if por_precio:
                margen = inv.fijar_precio(con, producto_id, precio_venta)
                precio = int(round(precio_escrito))
            else:
                precio = inv.fijar_margen(con, producto_id, margen_pct)
                margen = inv.leer_numero(margen_pct)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/productos#p{producto_id}")
    avisar(request, f"✅ {actual['nombre_interno']}: precio {inv.guaranies(precio)} "
                    f"(ganancia {f'{margen:g}'.replace('.', ',')}%).")
    return ir(f"/productos#p{producto_id}")


@app.get("/productos/{producto_id}/editar", response_class=HTMLResponse)
def producto_editar(request: Request, producto_id: int):
    with db.conexion() as con:
        producto = inv.obtener_producto(con, producto_id)
        if not producto:
            avisar(request, "Ese producto no existe.", "error")
            return ir("/productos")
        otros = [x for x in inv.listar_productos(con) if x["id"] != producto_id]
        return pagina(request, "producto_editar.html", p=producto,
                      categorias=inv.categorias(con), proveedores=inv.proveedores(con),
                      productos_json=opciones_productos(otros))


@app.post("/productos/{producto_id}/editar")
async def producto_editar_guardar(
    request: Request, producto_id: int,
    nombre_interno: str = Form(""), nombre_proveedor: str = Form(""), marca: str = Form(""),
    categoria_id: int = Form(...), margen_pct: str = Form(""),
    precio_venta: str = Form(""), activo: str = Form(""),
    imagen: UploadFile | None = File(None),
):
    ruta_imagen = await guardar_imagen(imagen, "productos")
    try:
        with db.conexion() as con:
            inv.actualizar_producto(con, producto_id, {
                "nombre_interno": nombre_interno, "nombre_proveedor": nombre_proveedor,
                "marca": marca, "categoria_id": categoria_id,
                "margen_pct": margen_pct, "precio_venta": precio_venta,
                "activo": activo == "1", "imagen": ruta_imagen,
            })
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/productos/{producto_id}/editar")
    avisar(request, "✅ Cambios guardados.")
    return ir(f"/productos#p{producto_id}")


@app.post("/productos/{producto_id}/unir")
def producto_unir(request: Request, producto_id: int, destino_id: str = Form("")):
    try:
        with db.conexion() as con:
            if not destino_id.isdigit():
                raise inv.ErrorNegocio("Elegí de la lista el producto correcto.")
            r = inv.unir_productos(con, producto_id, int(destino_id))
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/productos/{producto_id}/editar#unir")
    avisar(request, f"✅ Listo: la compra pasó a «{r['nombre']}». Ahora hay {inv.cantidad_txt(r['stock'])} "
                    f"en stock. Se borró el producto repetido ({r['borrado']}) y la próxima factura "
                    "con ese nombre se va a reconocer sola.")
    return ir(f"/productos/{destino_id}/editar")


@app.post("/productos/{producto_id}/activo")
def producto_activo(request: Request, producto_id: int):
    with db.conexion() as con:
        con.execute("UPDATE productos SET activo = 1 - activo WHERE id = ?", (producto_id,))
    return ir(f"/productos#p{producto_id}")


# ---------------------------------------------------------------- compras

@app.get("/compras", response_class=HTMLResponse)
def compras(request: Request):
    with db.conexion() as con:
        return pagina(request, "compras.html", compras=inv.listar_compras(con))


def _filtros_compras(request: Request) -> dict:
    q = request.query_params
    return {
        "proveedor_id": q.get("proveedor_id", "").strip() if q.get("proveedor_id", "").isdigit() else "",
        "producto_id": q.get("producto_id", "").strip() if q.get("producto_id", "").isdigit() else "",
        "desde": q.get("desde", "").strip(),
        "hasta": q.get("hasta", "").strip(),
    }


@app.get("/compras/excel")
def compras_excel(request: Request):
    with db.conexion() as con:
        return excel(exportar.compras(con, _filtros_compras(request)), "compras")


@app.get("/compras/panel", response_class=HTMLResponse)
def compras_panel(request: Request):
    filtros = _filtros_compras(request)
    with db.conexion() as con:
        filas = inv.listar_compras(con, **filtros)
        productos = inv.listar_productos(con)
        proveedores = inv.proveedores(con)
    por_proveedor: dict[str, float] = {}
    por_mes: dict[str, float] = {}
    for f in filas:
        por_proveedor[f["proveedor"] or "Sin proveedor"] = por_proveedor.get(f["proveedor"] or "Sin proveedor", 0) + f["costo_total"]
        por_mes[f["fecha"][:7]] = por_mes.get(f["fecha"][:7], 0) + f["costo_total"]
    elegido = next((p for p in productos if str(p["id"]) == filtros["producto_id"]), None)
    puntos = graficos.puntos_de_compras(filas) if elegido else []
    return pagina(
        request, "compras_panel.html", filas=filas, filtros=filtros, proveedores=proveedores,
        productos_json=opciones_productos(productos), producto_elegido=elegido,
        total=sum(f["costo_total"] for f in filas),
        facturas=len({f["compra_id"] for f in filas}),
        unidades=sum(f["cantidad"] for f in filas),
        por_proveedor=sorted(por_proveedor.items(), key=lambda x: -x[1]),
        por_mes=sorted(por_mes.items()),
        consulta=str(request.query_params),
        grafico_costos=graficos.linea_costos(puntos), resumen_costos=graficos.resumen_costos(puntos),
    )


@app.get("/compras/{lote_id}/editar", response_class=HTMLResponse)
def compra_editar(request: Request, lote_id: int):
    with db.conexion() as con:
        lote = inv.obtener_lote(con, lote_id)
        if not lote:
            avisar(request, "Esa compra no existe.", "error")
            return ir("/compras")
        return pagina(request, "compra_editar.html", c=lote, proveedores=inv.proveedores(con))


@app.post("/compras/{lote_id}/editar")
def compra_editar_guardar(
    request: Request, lote_id: int, fecha: str = Form(""), nro_factura: str = Form(""),
    proveedor: str = Form(""), cantidad: str = Form(""), costo_total: str = Form(""),
):
    try:
        with db.conexion() as con:
            inv.editar_lote(con, lote_id, {"fecha": fecha, "nro_factura": nro_factura,
                                           "proveedor": proveedor, "cantidad": cantidad,
                                           "costo_total": costo_total})
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/compras/{lote_id}/editar")
    avisar(request, "✅ Compra corregida.")
    return ir("/compras")


@app.post("/compras/{lote_id}/borrar")
def compra_borrar(request: Request, lote_id: int):
    try:
        with db.conexion() as con:
            inv.borrar_lote(con, lote_id)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/compras/{lote_id}/editar")
    avisar(request, "🗑️ Compra borrada.")
    return ir("/compras")


# ---------------------------------------------------------------- ventas

@app.get("/vender", response_class=HTMLResponse)
def vender(request: Request):
    with db.conexion() as con:
        productos = [p for p in inv.listar_productos(con, solo_activos=True) if p["stock"] > 0]
        clientes = inv.listar_clientes(con)
        return pagina(
            request, "vender.html", medios=MEDIOS_PAGO, hoy=date.today().isoformat(),
            productos_json=opciones_productos(productos),
            clientes_json=json_para_html([_cliente_opcion(c) for c in clientes]),
            ventas=inv.ultimas_ventas(con, 20),
        )


def _cliente_opcion(c) -> dict:
    detalle = " · ".join(x for x in (c["telefono"], c["documento"] and f"RUC/CI {c['documento']}") if x)
    return {"id": c["id"], "texto": c["nombre"], "detalle": detalle,
            "buscar": f"{c['telefono']} {c['documento']}"}


@app.post("/vender")
async def vender_guardar(request: Request):
    formulario = await request.form()
    ids = formulario.getlist("producto_id")
    cantidades = formulario.getlist("cantidad")
    cliente = formulario.get("cliente_id", "")
    try:
        items = [{"producto_id": int(i), "cantidad": c} for i, c in zip(ids, cantidades)]
        with db.conexion() as con:
            r = inv.registrar_venta(con, items, int(cliente) if str(cliente).isdigit() else None,
                                    formulario.get("medio_pago", ""), formulario.get("fecha", ""))
    except ValueError:
        avisar(request, "Hay un producto mal elegido. Probá de nuevo.", "error")
        return ir("/vender")
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/vender")
    avisar(request, f"✅ Venta guardada: {r['productos']} producto(s) por {inv.guaranies(r['total'])}. "
                    f"Ganancia {inv.guaranies(r['ganancia'])}.")
    return ir("/vender")


@app.get("/ventas/excel")
def ventas_excel():
    with db.conexion() as con:
        return excel(exportar.ventas(con), "ventas")


@app.post("/api/clientes")
async def api_cliente_nuevo(request: Request):
    datos = await request.json()
    try:
        with db.conexion() as con:
            c = inv.crear_cliente(con, datos.get("nombre", ""), datos.get("telefono", ""),
                                  datos.get("documento", ""))
    except inv.ErrorNegocio as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(_cliente_opcion(c))


# ---------------------------------------------------------------- etiquetas con código de barra

def tamano_etiqueta(con) -> str:
    return etiquetas_pdf.tamano_elegido(con)


@app.get("/etiquetas", response_class=HTMLResponse)
def etiquetas(request: Request, producto_id: str = ""):
    with db.conexion() as con:
        productos = inv.listar_productos(con, solo_activos=True)
        elegido = next((p for p in productos if str(p["id"]) == producto_id), None)
        ejemplo = elegido or next(iter(productos), None)
        return pagina(request, "etiquetas.html", facturas=inv.listar_facturas(con),
                      productos_json=opciones_productos(productos), elegido=elegido, ejemplo=ejemplo,
                      tamanos=TAMANOS, tamano=tamano_etiqueta(con),
                      impresora=impresora.configurada(), impresora_nombre=config.IMPRESORA_NOMBRE or config.IMPRESORA_IP)


@app.post("/etiquetas/tamano")
def etiquetas_tamano(request: Request, tamano: str = Form(""), producto_id: str = Form("")):
    if tamano in TAMANOS:
        with db.conexion() as con:
            inv.guardar_ajuste(con, "etiqueta_tamano", tamano)
        ancho, alto = TAMANOS[tamano]
        avisar(request, f"✅ Tamaño de etiqueta: {ancho} × {alto} mm.")
    return ir(f"/etiquetas?producto_id={producto_id}" if producto_id.isdigit() else "/etiquetas")


@app.get("/etiquetas/vista.svg")
def etiquetas_vista(producto_id: str = "", tamano: str = "", precio: str = ""):
    with db.conexion() as con:
        producto = inv.obtener_producto(con, int(producto_id)) if producto_id.isdigit() else None
        tamano = tamano if tamano in TAMANOS else tamano_etiqueta(con)
    if not producto:
        producto = {"nombre_interno": "Molde vela rosa mediano", "codigo_barra": "20022001",
                    "precio_venta": 30000, "codigo": "P-00001"}
    return Response(etiquetas_pdf.vista_svg(producto, tamano, precio != "no"), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


def _pdf(contenido: bytes, nombre: str) -> Response:
    nombre = re.sub(r"[^\w\-]+", "-", nombre).strip("-") or "etiquetas"
    return Response(contenido, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="etiquetas-{nombre}.pdf"'})


def _documento(lista: list, titulo: str, nombre: str, formato: str, con_precio: bool, con) -> Response:
    if formato == "a4":
        return _pdf(etiquetas_pdf.hoja_pdf(lista, titulo, con_precio), nombre + "-A4")
    return _pdf(etiquetas_pdf.rollo_pdf(lista, tamano_etiqueta(con), titulo, con_precio), nombre)


def _mandar_a_impresora(request: Request, lista: list, con) -> None:
    n = impresora.imprimir(lista, tamano_etiqueta(con))
    ancho, alto = TAMANOS[tamano_etiqueta(con)]
    avisar(request, f"🖨️ Se mandaron {n} etiqueta(s) de {ancho} × {alto} mm a la impresora.")


@app.get("/etiquetas/factura/{compra_id}.pdf")
def etiquetas_factura(request: Request, compra_id: int, formato: str = "rollo"):
    try:
        with db.conexion() as con:
            lista, nombre = etiquetas_pdf.etiquetas_de_factura(con, compra_id)
            return _documento(lista, f"Etiquetas factura {nombre}", nombre, formato, True, con)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/etiquetas")


@app.post("/etiquetas/factura/{compra_id}/imprimir")
def etiquetas_factura_imprimir(request: Request, compra_id: int):
    try:
        with db.conexion() as con:
            lista, _ = etiquetas_pdf.etiquetas_de_factura(con, compra_id)
            _mandar_a_impresora(request, lista, con)
    except (inv.ErrorNegocio, impresora.ErrorImpresora) as exc:
        avisar(request, f"😕 {exc}", "error")
    return ir("/etiquetas")


@app.post("/etiquetas/producto")
def etiquetas_producto(request: Request, producto_id: str = Form(""), cantidad: str = Form("1"),
                       precio: str = Form(""), accion: str = Form("pdf")):
    try:
        n = int(inv.leer_numero(cantidad, 0) or 0)
        if n <= 0:
            raise inv.ErrorNegocio("Escribí cuántas etiquetas querés (por ejemplo 10).")
        with db.conexion() as con:
            producto = inv.obtener_producto(con, int(producto_id)) if producto_id.isdigit() else None
            if not producto:
                raise inv.ErrorNegocio("Elegí el producto de la lista.")
            con_precio = precio != "no"
            if accion == "imprimir":
                impresora.enviar(impresora.tspl_etiquetas([(producto, n)], tamano_etiqueta(con), con_precio))
                ancho, alto = TAMANOS[tamano_etiqueta(con)]
                avisar(request, f"🖨️ Se mandaron {n} etiqueta(s) de {ancho} × {alto} mm a la impresora.")
                return ir(f"/etiquetas?producto_id={producto_id}")
            titulo = f"Etiquetas {producto['nombre_interno']}"
            return _documento([producto] * n, titulo, producto["codigo"], "a4" if accion == "a4" else "rollo",
                              con_precio, con)
    except (inv.ErrorNegocio, impresora.ErrorImpresora) as exc:
        avisar(request, f"😕 {exc}", "error")
        return ir(f"/etiquetas?producto_id={producto_id}")


@app.post("/etiquetas/prueba")
def etiquetas_prueba(request: Request):
    try:
        with db.conexion() as con:
            producto = next(iter(inv.listar_productos(con, solo_activos=True)), None)
            if not producto:
                raise inv.ErrorNegocio("Cargá al menos un producto para imprimir la prueba.")
            _mandar_a_impresora(request, [producto], con)
    except (inv.ErrorNegocio, impresora.ErrorImpresora) as exc:
        avisar(request, f"😕 {exc}", "error")
    return ir("/etiquetas")


# ---------------------------------------------------------------- datos auxiliares

@app.get("/api/stock")
def api_stock():
    """Stock actual de cada producto; la página lo consulta seguido para mantenerse al día."""
    with db.conexion() as con:
        filas = con.execute(
            "SELECT producto_id, SUM(cantidad_restante) FROM lotes GROUP BY producto_id"
        ).fetchall()
        total = con.execute("SELECT COUNT(*) FROM lotes").fetchone()[0]
    return JSONResponse({"version": total, "stock": {str(f[0]): f[1] for f in filas}})


@lru_cache(maxsize=2048)
def _svg_barra(codigo: str) -> bytes:
    salida = io.BytesIO()
    barcode.get("code128", codigo, writer=SVGWriter()).write(salida, options={
        "module_height": 10, "module_width": 0.25, "font_size": 7, "text_distance": 3.5,
        "quiet_zone": 2, "compress": False,
    })
    svg = salida.getvalue().decode("utf-8")
    # Se agrega viewBox para que el código se pueda agrandar o achicar sin cortarse (etiquetas).
    medidas = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', svg)
    if medidas:
        ancho, alto = (float(v) * 3.7795 for v in medidas.groups())
        svg = svg.replace(medidas.group(0), f'{medidas.group(0)} viewBox="0 0 {ancho:.2f} {alto:.2f}"', 1)
    return svg.encode("utf-8")


@app.get("/barra/{codigo}.svg")
def codigo_barra(codigo: str):
    if not re.fullmatch(r"[A-Za-z0-9\-]{1,40}", codigo):
        return Response(status_code=404)
    return Response(_svg_barra(codigo), media_type="image/svg+xml",
                    headers={"Cache-Control": "max-age=86400"})


@app.get("/archivo/{carpeta}/{nombre}")
def archivo(carpeta: str, nombre: str):
    if carpeta not in ("productos", "facturas") or not re.fullmatch(r"[\w\-]+\.(jpg|pdf)", nombre):
        return Response(status_code=404)
    ruta = config.ARCHIVOS_DIR / carpeta / nombre
    if not ruta.is_file():
        return Response(status_code=404)
    return FileResponse(ruta)

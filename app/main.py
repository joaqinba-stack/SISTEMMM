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

from app import codigos, config, db, etiquetas_pdf, exportar, graficos, impresora, inventario as inv, usuarios
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


SIN_SESION = ("/static", "/login", "/cuenta/nueva", "/cuenta/recuperar")
SOLO_ADMIN = ("/usuarios", "/actividad")
# Precios, costos y cantidades: solo el administrador. El stock baja con ventas; ajustarlo a mano,
# corregir o borrar una compra o unir productos (que mueve stock) es cosa del administrador.
SOLO_ADMIN_RUTAS = re.compile(r"^/(productos/\d+/(precio|margen|unir|stock)|compras/\d+/(editar|borrar))/?$")


@app.middleware("http")
async def exigir_sesion(request: Request, siguiente):
    """Sin una cuenta activa solo se ven las pantallas de entrada. Lo que cambia datos queda
    en el registro de actividad aunque la ruta no lo anote."""
    request.state.usuario = None
    request.state.anotado = False
    if request.session.get("uid"):
        with db.conexion() as con:
            cuenta = usuarios.obtener(con, request.session["uid"])
        if cuenta and cuenta["estado"] == "activo" and cuenta["sesion"] == request.session.get("sv"):
            request.state.usuario = cuenta
        else:  # la bloquearon o cambió la contraseña desde otro lado
            request.session.clear()
    ruta = request.url.path
    if request.state.usuario is None and not ruta.startswith(SIN_SESION):
        return RedirectResponse("/login", status_code=303)
    yo = request.state.usuario
    if yo is not None and yo["cambiar_clave"] and not (
            ruta in ("/cuenta", "/cuenta/clave") or ruta.startswith(("/static", "/salir"))):
        return RedirectResponse("/cuenta", status_code=303)
    if ruta.startswith(SOLO_ADMIN) and yo["rol"] != "admin":
        avisar(request, "Esa pantalla es solo para administradores.", "error")
        return RedirectResponse("/", status_code=303)
    if SOLO_ADMIN_RUTAS.match(ruta) and yo["rol"] != "admin":
        avisar(request, "Solo el administrador puede cambiar precios, costos y cantidades.", "error")
        volver = "/compras" if ruta.startswith("/compras") else "/productos"
        return RedirectResponse(volver, status_code=303)
    respuesta = await siguiente(request)
    if (request.method == "POST" and request.state.usuario is not None
            and not request.state.anotado and respuesta.status_code < 400):
        with db.conexion() as con:
            usuarios.registrar(con, request.state.usuario, "Acción", ruta)
    return respuesta


# Se agrega después para que corra antes que exigir_sesion (y la sesión ya exista).
app.add_middleware(SessionMiddleware, secret_key=config.clave_secreta(),
                   max_age=60 * 60 * 24 * 90, same_site="lax",
                   https_only=config.URL_PUBLICA.startswith("https://"))


# ---------------------------------------------------------------- utilidades

def avisar(request: Request, texto: str, tipo: str = "ok") -> None:
    request.session.setdefault("avisos", []).append([tipo, texto])
    if tipo == "error":  # lo que se rechazó no cambió nada: no va al registro de actividad
        request.state.anotado = True


def anotar(request: Request, accion: str, detalle: str = "") -> None:
    """Deja la acción en el registro de actividad, a nombre de quien está en sesión."""
    request.state.anotado = True
    with db.conexion() as con:
        usuarios.registrar(con, request.state.usuario, accion, detalle)


def es_admin(request: Request) -> bool:
    return bool(request.state.usuario) and request.state.usuario["rol"] == "admin"


def abrir_sesion(request: Request, cuenta) -> None:
    request.session.clear()
    request.session.update(uid=cuenta["id"], sv=cuenta["sesion"])


def pagina(request: Request, nombre: str, **contexto) -> HTMLResponse:
    avisos = request.session.pop("avisos", [])
    usuario = request.state.usuario
    with db.conexion() as con:
        pendientes = inv.notificaciones(con)
        por_aprobar = usuarios.pendientes(con) if usuario and usuario["rol"] == "admin" else 0
    cantidad = len({p["id"] for lista in pendientes.values() for p in lista})
    return plantillas.TemplateResponse(
        request, nombre,
        {"avisos": avisos, "usuario": usuario, "por_aprobar": por_aprobar,
         "cantidad_avisos": cantidad, **contexto},
    )


def pagina_codigo(request: Request, cuenta, codigo: str, motivo: str) -> HTMLResponse:
    """Muestra un código de recuperación recién creado. Es la única vez que se ve."""
    respuesta = pagina(request, "cuenta_codigo.html", cuenta=cuenta, codigo=codigo, motivo=motivo)
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


def json_para_html(datos) -> str:
    """JSON seguro para poner dentro de <script type="application/json">."""
    return json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")


def opciones_productos(productos) -> str:
    return json_para_html([
        {"id": p["id"], "texto": p["nombre_interno"],
         "detalle": f"{p['codigo']} · {inv.guaranies(p['precio_venta'])} · hay {inv.cantidad_txt(p['stock'])}",
         "buscar": p["codigo"],
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
    if request.state.usuario:
        return ir("/")
    with db.conexion() as con:
        sin_cuentas = not usuarios.listar(con)
    return pagina(request, "login.html", sin_cuentas=sin_cuentas)


@app.post("/login")
def login_enviar(request: Request, usuario: str = Form(""), clave: str = Form("")):
    with db.conexion() as con:
        cuenta, error = usuarios.entrar(con, usuario, clave)
        if cuenta:
            usuarios.registrar(con, cuenta, "Entró al sistema")
    if not cuenta:
        avisar(request, error, "error")
        return pagina(request, "login.html", usuario_escrito=usuario.strip())
    abrir_sesion(request, cuenta)
    return ir("/")


@app.get("/salir")
def salir(request: Request):
    anotar(request, "Salió del sistema")
    request.session.clear()
    return ir("/login")


@app.get("/cuenta/nueva", response_class=HTMLResponse)
def cuenta_nueva(request: Request):
    """Solo para crear la primera cuenta (la del administrador) en una instalación nueva."""
    with db.conexion() as con:
        if usuarios.listar(con):
            return _cuentas_las_crea_el_admin(request)
    return pagina(request, "cuenta_nueva.html", sin_cuentas=True)


def _cuentas_las_crea_el_admin(request: Request) -> RedirectResponse:
    avisar(request, "Las cuentas las crea el administrador. Pedile que te dé de alta.", "error")
    return ir("/login")


@app.post("/cuenta/nueva")
def cuenta_nueva_guardar(request: Request, nombre: str = Form(""), usuario: str = Form(""),
                         clave: str = Form(""), repetir: str = Form("")):
    try:
        with db.conexion() as con:
            if usuarios.listar(con):
                return _cuentas_las_crea_el_admin(request)
            cuenta, codigo = usuarios.crear(con, usuario, nombre, clave, repetir)
            usuarios.registrar(con, cuenta, "Creó su cuenta", usuarios.ESTADOS[cuenta["estado"]])
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return pagina(request, "cuenta_nueva.html", sin_cuentas=True,
                      nombre_escrito=nombre.strip(), usuario_escrito=usuario.strip())
    abrir_sesion(request, cuenta)  # la primera cuenta es la del administrador y entra enseguida
    return pagina_codigo(request, cuenta, codigo, "nueva")


@app.get("/cuenta/recuperar", response_class=HTMLResponse)
def cuenta_recuperar(request: Request):
    return pagina(request, "cuenta_recuperar.html")


@app.post("/cuenta/recuperar")
def cuenta_recuperar_guardar(request: Request, usuario: str = Form(""), codigo: str = Form(""),
                             clave: str = Form(""), repetir: str = Form("")):
    with db.conexion() as con:
        nuevo, error = usuarios.recuperar(con, usuario, codigo, clave, repetir)
    if not nuevo:
        avisar(request, error, "error")
        return pagina(request, "cuenta_recuperar.html", usuario_escrito=usuario.strip())
    request.session.clear()
    return pagina_codigo(request, None, nuevo, "recuperada")


@app.get("/cuenta", response_class=HTMLResponse)
def cuenta(request: Request):
    return pagina(request, "cuenta.html", roles=usuarios.ROLES)


@app.post("/cuenta/clave")
def cuenta_clave(request: Request, actual: str = Form(""), clave: str = Form(""), repetir: str = Form("")):
    yo = request.state.usuario
    try:
        with db.conexion() as con:
            request.session["sv"] = usuarios.cambiar_clave(con, yo["id"], actual, clave, repetir)
            # La primera vez (venía con la contraseña temporal del administrador) recibe su código.
            codigo = usuarios.regenerar_codigo(con, yo["id"]) if yo["cambiar_clave"] else None
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/cuenta")
    if codigo:
        anotar(request, "Eligió su contraseña", "Reemplazó la contraseña temporal")
        return pagina_codigo(request, yo, codigo, "primera")
    anotar(request, "Cambió su contraseña")
    avisar(request, "✅ Contraseña cambiada. Las otras sesiones abiertas con esta cuenta se cerraron.")
    return ir("/cuenta")


@app.post("/cuenta/codigo")
def cuenta_codigo(request: Request, actual: str = Form("")):
    yo = request.state.usuario
    with db.conexion() as con:
        if not usuarios.clave_correcta(con, yo["id"], actual):
            avisar(request, "La contraseña actual no es correcta.", "error")
            return ir("/cuenta")
        codigo = usuarios.regenerar_codigo(con, yo["id"])
    anotar(request, "Generó un código de recuperación nuevo")
    return pagina_codigo(request, yo, codigo, "propio")


# ---------------------------------------------------------------- usuarios (solo administradores)

@app.get("/usuarios", response_class=HTMLResponse)
def usuarios_lista(request: Request):
    with db.conexion() as con:
        return pagina(request, "usuarios.html", cuentas=usuarios.listar(con),
                      roles=usuarios.ROLES, estados=usuarios.ESTADOS)


@app.post("/usuarios/nuevo")
def usuario_nuevo(request: Request, nombre: str = Form(""), usuario: str = Form(""),
                  clave: str = Form(""), rol: str = Form("usuario")):
    """El administrador da de alta una cuenta con una contraseña temporal; la persona elige la
    suya al entrar por primera vez y ahí recibe su código de recuperación."""
    try:
        with db.conexion() as con:
            cuenta, _ = usuarios.crear(con, usuario, nombre, clave, rol=rol, clave_temporal=True)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/usuarios")
    anotar(request, "Creó un usuario", f"{cuenta['nombre']} ({cuenta['usuario']}), {usuarios.ROLES[cuenta['rol']]}")
    avisar(request, f"✅ Cuenta creada. Pasale a {cuenta['nombre']} el usuario «{cuenta['usuario']}» y la "
                    "contraseña temporal: la va a tener que cambiar al entrar.")
    return ir("/usuarios")


@app.post("/usuarios/{usuario_id}/estado")
def usuario_estado(request: Request, usuario_id: int, estado: str = Form("")):
    try:
        with db.conexion() as con:
            cuenta = usuarios.cambiar_estado(con, usuario_id, estado)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/usuarios")
    hecho = "Habilitó la cuenta" if estado == "activo" else "Bloqueó la cuenta"
    anotar(request, hecho, f"{cuenta['nombre']} ({cuenta['usuario']})")
    avisar(request, f"✅ {hecho} de {cuenta['nombre']}.")
    return ir("/usuarios")


@app.post("/usuarios/{usuario_id}/rol")
def usuario_rol(request: Request, usuario_id: int, rol: str = Form("")):
    try:
        with db.conexion() as con:
            cuenta = usuarios.cambiar_rol(con, usuario_id, rol)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/usuarios")
    anotar(request, "Cambió el perfil de una cuenta",
           f"{cuenta['nombre']} ({cuenta['usuario']}) ahora es {usuarios.ROLES[rol]}")
    avisar(request, f"✅ {cuenta['nombre']} ahora es {usuarios.ROLES[rol]}.")
    return ir("/usuarios")


@app.post("/usuarios/{usuario_id}/codigo")
def usuario_codigo(request: Request, usuario_id: int):
    """Para quien olvidó la contraseña y perdió su código: el administrador le genera otro."""
    try:
        with db.conexion() as con:
            codigo = usuarios.regenerar_codigo(con, usuario_id)
            cuenta = usuarios.obtener(con, usuario_id)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/usuarios")
    anotar(request, "Generó un código de recuperación para otra cuenta",
           f"{cuenta['nombre']} ({cuenta['usuario']})")
    return pagina_codigo(request, cuenta, codigo, "admin")


@app.get("/actividad", response_class=HTMLResponse)
def actividad(request: Request, usuario_id: str = "", desde: str = "", hasta: str = ""):
    filtros = {"usuario_id": usuario_id if usuario_id.isdigit() else "",
               "desde": desde.strip(), "hasta": hasta.strip()}
    with db.conexion() as con:
        return pagina(request, "actividad.html", filtros=filtros, cuentas=usuarios.listar(con),
                      filas=usuarios.actividad(con, **filtros), limite=500)


@app.get("/reportes/ventas", response_class=HTMLResponse)
def reporte_ventas(request: Request, usuario_id: str = "", desde: str = "", hasta: str = ""):
    """Lo que vendió cada cuenta. Quien no es administrador ve solo lo suyo."""
    yo = request.state.usuario
    hoy = date.today()
    filtros = {"desde": desde.strip() or hoy.replace(day=1).isoformat(),
               "hasta": hasta.strip() or hoy.isoformat(),
               "usuario_id": usuario_id if usuario_id.isdigit() else ""}
    if yo["rol"] != "admin":
        filtros["usuario_id"] = str(yo["id"])
    de_quien = int(filtros["usuario_id"]) if filtros["usuario_id"] else None  # 0 = sin usuario
    with db.conexion() as con:
        resumen = inv.ventas_por_usuario(con, filtros["desde"], filtros["hasta"], de_quien)
        ventas = inv.ultimas_ventas(con, 300, desde=filtros["desde"], hasta=filtros["hasta"],
                                    usuario_id=de_quien)
        cuentas = usuarios.listar(con)
    return pagina(
        request, "reporte_ventas.html", filtros=filtros, resumen=resumen, ventas=ventas, cuentas=cuentas,
        total=sum(r["total"] for r in resumen), ganancia=sum(r["ganancia"] for r in resumen),
        cantidad=sum(r["ventas"] for r in resumen),
    )


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
    datos = {"nombre_interno": nombre_interno, "marca": marca, "categoria_id": categoria_id,
             "nombre_proveedor": nombre_proveedor, "imagen": ruta_imagen}
    if es_admin(request):
        datos.update({"proveedor": proveedor, "fecha": fecha, "nro_factura": nro_factura,
                      "cantidad": cantidad, "costo_total": costo_total,
                      "margen_pct": margen_pct, "precio_venta": precio_venta})
    else:  # sin compra ni precios: queda con stock 0 para que el administrador los complete
        datos.update({"cantidad": 0, "costo_total": 0, "revisar": True})
    try:
        with db.conexion() as con:
            creado = inv.crear_producto(con, datos)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/productos/nuevo")
    anotar(request, "Creó un producto", f"{creado['codigo']} {nombre_interno.strip()}")
    avisar(request, f"✅ Producto guardado. Su código es {creado['codigo']}.")
    return ir(f"/productos#p{creado['id']}")


@app.get("/productos", response_class=HTMLResponse)
def productos(request: Request):
    with db.conexion() as con:
        return pagina(request, "productos.html", productos=inv.listar_productos(con),
                      categorias=inv.categorias(con), margen=config.MARGEN_POR_DEFECTO)


@app.get("/productos/excel")
def productos_excel(request: Request):
    anotar(request, "Descargó un Excel", "Productos")
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
    anotar(request, "Cambió un precio",
           f"{actual['codigo']} {actual['nombre_interno']}: de {inv.guaranies(actual['precio_venta'])} "
           f"a {inv.guaranies(precio)}")
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
                      productos_json=opciones_productos(otros),
                      ajustes=inv.listar_ajustes(con, producto_id) if es_admin(request) else [])


def _anotar_ajuste(request: Request, r: dict, motivo: str) -> None:
    p, motivo = r["producto"], " ".join((motivo or "").split()) or "Corrección manual"
    cambio = f"+{inv.cantidad_txt(r['diferencia'])}" if r["diferencia"] > 0 else inv.cantidad_txt(r["diferencia"])
    anotar(request, "Ajustó el stock",
           f"{p['codigo']} {p['nombre_interno']}: de {inv.cantidad_txt(r['antes'])} "
           f"a {inv.cantidad_txt(r['despues'])} ({cambio}). Motivo: {motivo}")


@app.post("/productos/{producto_id}/stock")
def producto_ajustar_stock(request: Request, producto_id: int, cantidad: str = Form(""),
                           motivo: str = Form(""), volver: str = Form("")):
    """El administrador corrige el stock: escribe cuántas unidades hay (y si quiere, por qué),
    desde la ficha del producto o directo en la lista de productos."""
    destino = f"/productos#p{producto_id}" if volver == "lista" else f"/productos/{producto_id}/editar#stock"
    try:
        with db.conexion() as con:
            r = inv.ajustar_stock(con, producto_id, cantidad, motivo, request.state.usuario)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(destino)
    p = r["producto"]
    _anotar_ajuste(request, r, motivo)
    avisar(request, f"✅ {p['nombre_interno']}: ahora hay {inv.cantidad_txt(r['despues'])} en stock "
                    f"(antes {inv.cantidad_txt(r['antes'])}).")
    return ir(destino)


@app.post("/productos/{producto_id}/editar")
async def producto_editar_guardar(
    request: Request, producto_id: int,
    nombre_interno: str = Form(""), nombre_proveedor: str = Form(""), marca: str = Form(""),
    categoria_id: int = Form(...), margen_pct: str = Form(""),
    precio_venta: str = Form(""), activo: str = Form(""),
    stock: str = Form(""), stock_antes: str = Form(""), motivo_stock: str = Form(""),
    imagen: UploadFile | None = File(None),
):
    ruta_imagen = await guardar_imagen(imagen, "productos")
    ajuste = None
    admin = es_admin(request)
    datos = {"nombre_interno": nombre_interno, "nombre_proveedor": nombre_proveedor,
             "marca": marca, "categoria_id": categoria_id,
             "activo": activo == "1", "imagen": ruta_imagen}
    if admin:  # el % y el precio los cambia solo el administrador
        datos.update({"margen_pct": margen_pct, "precio_venta": precio_venta})
    try:
        with db.conexion() as con:
            inv.actualizar_producto(con, producto_id, datos, quitar_revisar=admin)
            # el stock solo cambia si el administrador tocó el número (no si cambió solo mientras tanto)
            nuevo = inv.leer_numero(stock)
            if (admin and nuevo is not None and nuevo != inv.leer_numero(stock_antes)
                    and round(nuevo, 6) != inv.stock(con, producto_id)):
                ajuste = inv.ajustar_stock(con, producto_id, stock, motivo_stock, request.state.usuario)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/productos/{producto_id}/editar")
    anotar(request, "Editó un producto", f"{nombre_interno.strip()} (n.º {producto_id})")
    if ajuste:
        _anotar_ajuste(request, ajuste, motivo_stock)
        avisar(request, f"✅ Cambios guardados. Ahora hay {inv.cantidad_txt(ajuste['despues'])} en stock "
                        f"(antes {inv.cantidad_txt(ajuste['antes'])}).")
    else:
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
    anotar(request, "Unió dos productos", f"{r['borrado']} pasó a «{r['nombre']}»")
    avisar(request, f"✅ Listo: la compra pasó a «{r['nombre']}». Ahora hay {inv.cantidad_txt(r['stock'])} "
                    f"en stock. Se borró el producto repetido ({r['borrado']}) y la próxima factura "
                    "con ese nombre se va a reconocer sola.")
    return ir(f"/productos/{destino_id}/editar")


@app.post("/productos/{producto_id}/activo")
def producto_activo(request: Request, producto_id: int, activo: str = Form("")):
    """El interruptor Sí / No de la lista: activo=1 o 0 lo deja así; sin valor, lo da vuelta."""
    with db.conexion() as con:
        antes = inv.obtener_producto(con, producto_id)
        if antes:
            nuevo = int(activo) if activo in ("0", "1") else 1 - antes["activo"]
            con.execute("UPDATE productos SET activo = ? WHERE id = ?", (nuevo, producto_id))
    if antes and nuevo != antes["activo"]:
        anotar(request, "Activó un producto" if nuevo else "Desactivó un producto",
               f"{antes['codigo']} {antes['nombre_interno']}")
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
    anotar(request, "Descargó un Excel", "Compras")
    with db.conexion() as con:
        return excel(exportar.compras(con, _filtros_compras(request)), "compras")


@app.get("/compras/panel", response_class=HTMLResponse)
def compras_panel(request: Request):
    filtros = _filtros_compras(request)
    with db.conexion() as con:
        # los ajustes de stock no son plata gastada: no cuentan en el panel
        filas = [f for f in inv.listar_compras(con, **filtros) if f["origen"] != "ajuste"]
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


def _detalle_lote(lote) -> str:
    """Una compra en una línea, para el registro de actividad."""
    if not lote:
        return "compra que ya no existe"
    return (f"{lote['codigo']} {lote['nombre_interno']}, factura {lote['nro_factura'] or 's/n'} de "
            f"{lote['proveedor'] or 'sin proveedor'}, {inv.cantidad_txt(lote['cantidad'])} por "
            f"{inv.guaranies(lote['costo_total'])}")


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
            antes = inv.obtener_lote(con, lote_id)
            inv.editar_lote(con, lote_id, {"fecha": fecha, "nro_factura": nro_factura,
                                           "proveedor": proveedor, "cantidad": cantidad,
                                           "costo_total": costo_total})
            ahora = inv.obtener_lote(con, lote_id)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/compras/{lote_id}/editar")
    anotar(request, "Corrigió una compra", _detalle_lote(antes) + " → " + _detalle_lote(ahora))
    avisar(request, "✅ Compra corregida.")
    return ir("/compras")


@app.post("/compras/{lote_id}/borrar")
def compra_borrar(request: Request, lote_id: int):
    try:
        with db.conexion() as con:
            antes = inv.obtener_lote(con, lote_id)
            inv.borrar_lote(con, lote_id)
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir(f"/compras/{lote_id}/editar")
    anotar(request, "Borró una compra", _detalle_lote(antes))
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
                                    formulario.get("medio_pago", ""), formulario.get("fecha", ""),
                                    usuario_id=request.state.usuario["id"])
    except ValueError:
        avisar(request, "Hay un producto mal elegido. Probá de nuevo.", "error")
        return ir("/vender")
    except inv.ErrorNegocio as exc:
        avisar(request, str(exc), "error")
        return ir("/vender")
    anotar(request, "Registró una venta",
           f"Venta n.º {r['venta_id']}: {r['productos']} producto(s) por {inv.guaranies(r['total'])}")
    avisar(request, f"✅ Venta guardada: {r['productos']} producto(s) por {inv.guaranies(r['total'])}. "
                    f"Ganancia {inv.guaranies(r['ganancia'])}.")
    return ir("/vender")


@app.get("/ventas/excel")
def ventas_excel(request: Request):
    anotar(request, "Descargó un Excel", "Ventas")
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
    anotar(request, "Creó un cliente", c["nombre"])
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
        anotar(request, "Cambió el tamaño de etiqueta", f"{ancho} × {alto} mm")
        avisar(request, f"✅ Tamaño de etiqueta: {ancho} × {alto} mm.")
    return ir(f"/etiquetas?producto_id={producto_id}" if producto_id.isdigit() else "/etiquetas")


@app.get("/etiquetas/vista.svg")
def etiquetas_vista(producto_id: str = "", tamano: str = "", precio: str = ""):
    with db.conexion() as con:
        producto = inv.obtener_producto(con, int(producto_id)) if producto_id.isdigit() else None
        tamano = tamano if tamano in TAMANOS else tamano_etiqueta(con)
    if not producto:
        producto = {"nombre_interno": "Molde vela rosa mediano", "codigo_barra": "P-00001",
                    "precio_venta": 30000, "codigo": "P-00001"}
    return Response(etiquetas_pdf.vista_svg(producto, tamano, precio != "no"), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


def _pdf(contenido: bytes, nombre: str, en_pantalla: bool = False) -> Response:
    """en_pantalla: se abre en el navegador (para imprimir) en vez de descargarse."""
    nombre = re.sub(r"[^\w\-]+", "-", nombre).strip("-") or "etiquetas"
    modo = "inline" if en_pantalla else "attachment"
    return Response(contenido, media_type="application/pdf",
                    headers={"Content-Disposition": f'{modo}; filename="etiquetas-{nombre}.pdf"'})


def _documento(lista: list, titulo: str, nombre: str, formato: str, con_precio: bool, con,
               imprimir: bool = False) -> Response:
    """imprimir: el PDF del rollo se abre mostrando la ventana de impresión del navegador, para
    la impresora conectada a la computadora desde la que se entra (no a la del sistema)."""
    if formato == "a4":
        return _pdf(etiquetas_pdf.hoja_pdf(lista, titulo, con_precio), nombre + "-A4")
    return _pdf(etiquetas_pdf.rollo_pdf(lista, tamano_etiqueta(con), titulo, con_precio, imprimir),
                nombre, en_pantalla=imprimir)


def _mandar_a_impresora(request: Request, lista: list, con) -> None:
    n = impresora.imprimir(lista, tamano_etiqueta(con))
    ancho, alto = TAMANOS[tamano_etiqueta(con)]
    usuarios.registrar(con, request.state.usuario, "Imprimió etiquetas", f"{n} etiqueta(s) de {ancho} × {alto} mm")
    request.state.anotado = True
    avisar(request, f"🖨️ Se mandaron {n} etiqueta(s) de {ancho} × {alto} mm a la impresora.")


@app.get("/etiquetas/factura/{compra_id}.pdf")
def etiquetas_factura(request: Request, compra_id: int, formato: str = "rollo", imprimir: str = ""):
    try:
        with db.conexion() as con:
            lista, nombre = etiquetas_pdf.etiquetas_de_factura(con, compra_id)
            usuarios.registrar(con, request.state.usuario,
                               "Imprimió etiquetas" if imprimir else "Descargó etiquetas en PDF",
                               f"{len(lista)} de la factura {nombre}")
            return _documento(lista, f"Etiquetas factura {nombre}", nombre, formato, True, con,
                              imprimir=bool(imprimir))
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
                usuarios.registrar(con, request.state.usuario, "Imprimió etiquetas",
                                   f"{n} de {producto['codigo']} {producto['nombre_interno']}")
                request.state.anotado = True
                avisar(request, f"🖨️ Se mandaron {n} etiqueta(s) de {ancho} × {alto} mm a la impresora.")
                return ir(f"/etiquetas?producto_id={producto_id}")
            en_navegador = accion == "navegador"  # la impresora está en la computadora de quien mira
            usuarios.registrar(con, request.state.usuario,
                               "Imprimió etiquetas" if en_navegador else "Descargó etiquetas en PDF",
                               f"{n} de {producto['codigo']} {producto['nombre_interno']}")
            request.state.anotado = True
            titulo = f"Etiquetas {producto['nombre_interno']}"
            return _documento([producto] * n, titulo, producto["codigo"], "a4" if accion == "a4" else "rollo",
                              con_precio, con, imprimir=en_navegador)
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

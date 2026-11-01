import re

from fastapi.testclient import TestClient

from app import db, inventario as inv, usuarios
from app.main import app

CLAVE = "clave-de-prueba"
TEMPORAL = "temporal-1234"


def crear(c, usuario, nombre=None, clave=CLAVE):
    return c.post("/cuenta/nueva", data={"nombre": nombre or usuario.capitalize(), "usuario": usuario,
                                         "clave": clave, "repetir": clave})


def codigo_en(r) -> str:
    return re.search(r'id="codigo">([A-Z0-9-]+)<', r.text).group(1)


def entrar(c, usuario, clave=CLAVE):
    return c.post("/login", data={"usuario": usuario, "clave": clave})


def alta(admin, usuario, rol="usuario", clave=TEMPORAL):
    """El administrador da de alta una cuenta con contraseña temporal."""
    return admin.post("/usuarios/nuevo", data={"nombre": usuario.capitalize(), "usuario": usuario,
                                               "clave": clave, "rol": rol})


def primer_ingreso(c, usuario, clave=CLAVE):
    """Entra con la contraseña temporal y elige la suya; devuelve la página con su código."""
    entrar(c, usuario, TEMPORAL)
    return c.post("/cuenta/clave", data={"actual": TEMPORAL, "clave": clave, "repetir": clave})


def producto(c, nombre="Molde A", cantidad="5", precio="3.000"):
    return c.post("/productos/nuevo", data={
        "nombre_interno": nombre, "categoria_id": "1", "proveedor": "Pacific", "fecha": "2026-06-05",
        "cantidad": cantidad, "costo_total": "5000", "margen_pct": "40", "precio_venta": precio})


def test_la_primera_cuenta_administra_y_da_de_alta_las_demas():
    ana, beto = TestClient(app), TestClient(app)
    r = crear(ana, "ana")
    assert "sos el administrador" in r.text and re.fullmatch(r"(\w{4}-){2}\w{4}", codigo_en(r))
    assert r.headers["cache-control"] == "no-store"
    assert ana.get("/usuarios").url.path == "/usuarios"

    # ya hay cuentas: nadie se crea una desde la entrada
    r = beto.get("/cuenta/nueva")
    assert r.url.path == "/login" and "las crea el administrador" in r.text
    assert "las crea el administrador" in crear(beto, "beto").text
    assert ana.get("/usuarios").text.count('data-titulo="Usuario"') == 1  # sigue habiendo una sola
    r = beto.get("/login")
    assert 'href="/cuenta/nueva"' not in r.text and 'href="/cuenta/recuperar"' in r.text

    assert "Cuenta creada" in alta(ana, "beto").text
    assert "Todavía no eligió su contraseña" in ana.get("/usuarios").text
    r = entrar(beto, "beto", TEMPORAL)  # con la temporal solo puede elegir la suya
    assert r.url.path == "/cuenta" and "Elegí tu contraseña" in r.text
    assert beto.get("/productos").url.path == "/cuenta"
    r = beto.post("/cuenta/clave", data={"actual": TEMPORAL, "clave": TEMPORAL, "repetir": TEMPORAL})
    assert "distinta de la actual" in r.text
    r = beto.post("/cuenta/clave", data={"actual": TEMPORAL, "clave": CLAVE, "repetir": CLAVE})
    assert "Listo, Beto" in r.text and r.headers["cache-control"] == "no-store"
    codigo_beto = codigo_en(r)
    assert beto.get("/productos").url.path == "/productos"
    assert "Todavía no eligió su contraseña" not in ana.get("/usuarios").text

    r = beto.get("/usuarios")  # no es administrador
    assert r.url.path == "/" and "solo para administradores" in r.text
    assert beto.get("/actividad").url.path == "/"
    beto.post("/usuarios/nuevo", data={"nombre": "Ceci", "usuario": "ceci", "clave": TEMPORAL, "rol": "admin"})
    assert "ceci" not in ana.get("/usuarios").text  # beto no puede crear cuentas

    # el código que recibió al elegir su contraseña le sirve si la olvida
    r = TestClient(app).post("/cuenta/recuperar", data={"usuario": "beto", "codigo": codigo_beto,
                                                        "clave": "otra-clave-9", "repetir": "otra-clave-9"})
    assert "Contraseña cambiada" in r.text
    assert entrar(beto, "beto", "otra-clave-9").url.path == "/"

    ana.post("/usuarios/2/estado", data={"estado": "bloqueado"})
    assert beto.get("/productos").url.path == "/login"  # la sesión abierta se corta
    assert "está bloqueada" in entrar(beto, "beto", "otra-clave-9").text


def test_no_acepta_cuentas_mal_armadas():
    c = TestClient(app)
    assert "al menos 8" in crear(c, "ana", clave="corta").text
    assert "entre 3 y 30" in crear(c, "a b").text
    r = c.post("/cuenta/nueva", data={"nombre": "Ana", "usuario": "ana", "clave": CLAVE, "repetir": CLAVE + "x"})
    assert "no son iguales" in r.text and 'value="ana"' in r.text
    crear(c, "ana")
    assert "Ya hay una cuenta" in alta(c, "ANA").text
    assert "al menos 8" in alta(c, "beto", clave="corta").text
    assert "Elegí el perfil" in alta(c, "beto", rol="jefe").text
    assert "Cuenta creada" in alta(c, "ceci", rol="admin").text
    assert c.get("/usuarios").text.count("<td data-titulo=\"Perfil\">Administrador</td>") == 2


def test_recuperar_la_contrasena_con_el_codigo_sin_correo():
    ana = TestClient(app)
    codigo = codigo_en(crear(ana, "ana"))
    otro = TestClient(app)
    assert "Código de recuperación" in otro.get("/cuenta/recuperar").text
    datos = {"usuario": "ana", "clave": "contraseña-nueva", "repetir": "contraseña-nueva"}
    assert "no son correctos" in otro.post("/cuenta/recuperar", data={**datos, "codigo": "AAAA-BBBB-CCCC"}).text

    r = otro.post("/cuenta/recuperar", data={**datos, "codigo": codigo.lower().replace("-", " ")})
    assert "Contraseña cambiada" in r.text and codigo_en(r) != codigo
    assert ana.get("/productos").url.path == "/login"  # las sesiones viejas se cierran
    assert "no son correctos" in entrar(otro, "ana").text
    assert entrar(otro, "ana", "contraseña-nueva").url.path == "/"
    # el código usado ya no sirve
    assert "no son correctos" in TestClient(app).post("/cuenta/recuperar", data={**datos, "codigo": codigo}).text


def test_el_administrador_genera_un_codigo_para_quien_lo_perdio():
    ana, beto = TestClient(app), TestClient(app)
    crear(ana, "ana")
    alta(ana, "beto")
    primer_ingreso(beto, "beto")
    r = ana.post("/usuarios/2/codigo")
    assert "Código para Beto" in r.text
    r = beto.post("/cuenta/recuperar", data={"usuario": "beto", "codigo": codigo_en(r),
                                             "clave": "otra-clave-1", "repetir": "otra-clave-1"})
    assert "Contraseña cambiada" in r.text
    assert entrar(beto, "beto", "otra-clave-1").url.path == "/"


def test_demasiados_intentos_frenan_la_cuenta():
    c = TestClient(app)
    crear(c, "ana")
    c.get("/salir")
    for _ in range(usuarios.MAX_INTENTOS - 1):
        assert "no son correctos" in entrar(c, "ana", "adivinando").text
    assert "Demasiados intentos" in entrar(c, "ana", "adivinando").text
    assert "Demasiados intentos" in entrar(c, "ana").text  # ni con la correcta, hasta que pase el tiempo
    with db.conexion() as con:
        con.execute("UPDATE usuarios SET bloqueado_hasta = '2000-01-01T00:00:00'")
    assert entrar(c, "ana").url.path == "/"


def test_cambiar_contrasena_y_codigo_desde_mi_cuenta():
    c, otra_sesion = TestClient(app), TestClient(app)
    crear(c, "ana")
    entrar(otra_sesion, "ana")
    assert "no es correcta" in c.post("/cuenta/clave", data={"actual": "mal", "clave": "nueva-clave-1",
                                                             "repetir": "nueva-clave-1"}).text
    r = c.post("/cuenta/clave", data={"actual": CLAVE, "clave": "nueva-clave-1", "repetir": "nueva-clave-1"})
    assert "Contraseña cambiada" in r.text and r.url.path == "/cuenta"
    assert otra_sesion.get("/productos").url.path == "/login"
    assert "no es correcta" in c.post("/cuenta/codigo", data={"actual": CLAVE}).text
    assert codigo_en(c.post("/cuenta/codigo", data={"actual": "nueva-clave-1"}))


def test_tiene_que_quedar_un_administrador():
    c = TestClient(app)
    crear(c, "ana")
    assert "al menos un administrador" in c.post("/usuarios/1/estado", data={"estado": "bloqueado"}).text
    assert "al menos un administrador" in c.post("/usuarios/1/rol", data={"rol": "usuario"}).text
    assert c.get("/usuarios").url.path == "/usuarios"


def test_ventas_por_usuario_y_registro_de_actividad():
    ana, beto = TestClient(app), TestClient(app)
    crear(ana, "ana")
    alta(ana, "beto")
    primer_ingreso(beto, "beto")
    producto(ana)
    ana.post("/vender", data={"producto_id": ["1"], "cantidad": ["2"], "medio_pago": "Efectivo"})
    beto.post("/vender", data={"producto_id": ["1"], "cantidad": ["1"], "medio_pago": "Efectivo"})
    beto.post("/vender", data={"producto_id": ["1"], "cantidad": ["99"]})  # rechazada: no queda en el registro

    r = ana.get("/reportes/ventas")
    tabla = r.text.split('id="tabla-vendedores"')[1].split("</table>")[0]
    assert tabla.index("Ana") < tabla.index("Beto")  # primero quien más vendió
    assert "₲ 6.000" in tabla and "₲ 3.000" in tabla and "67%" in tabla and "₲ 9.000" in r.text
    r = ana.get("/reportes/ventas?usuario_id=2")
    assert "<b>Beto</b>" in r.text and "<b>Ana</b>" not in r.text
    assert "No hay ventas" in ana.get("/reportes/ventas?desde=2000-01-01&hasta=2000-01-31").text

    r = beto.get("/reportes/ventas?usuario_id=1")  # quien no administra ve solo lo suyo
    assert "Mis ventas" in r.text and "<b>Ana</b>" not in r.text and "₲ 3.000" in r.text
    assert "<td data-titulo=\"Vendió\">Beto</td>" in beto.get("/vender").text

    r = ana.get("/actividad")
    for hecho in ("Creó su cuenta", "Creó un usuario", "Eligió su contraseña", "Entró al sistema",
                  "Creó un producto",
                  "Registró una venta", "Venta n.º 2: 1 producto(s) por ₲ 3.000"):
        assert hecho in r.text
    assert r.text.count("Registró una venta") == 2
    solo_beto = ana.get("/actividad?usuario_id=2").text
    assert "Registró una venta" in solo_beto and "Creó un producto" not in solo_beto

    ana.post("/productos/1/activo", data={"activo": "0"})
    ana.post("/productos/1/activo", data={"activo": "0"})  # ya estaba en No: no cambia ni se anota
    ana.get("/ventas/excel")
    r = ana.get("/actividad")
    assert "Desactivó un producto" in r.text and "Descargó un Excel" in r.text
    assert r.text.count("Desactivó un producto") == 1
    with db.conexion() as con:
        assert inv.obtener_producto(con, 1)["activo"] == 0
    r = ana.get("/productos")  # se ve igual que los activos, con el interruptor en No
    assert 'id="p1" class="inactivo' in r.text and 'aria-pressed="true" title="Inactivo' in r.text
    ana.post("/productos/1/activo", data={"activo": "1"})
    with db.conexion() as con:
        assert inv.obtener_producto(con, 1)["activo"] == 1
    ana.post("/productos/1/activo")  # sin valor lo da vuelta (como antes)
    with db.conexion() as con:
        assert inv.obtener_producto(con, 1)["activo"] == 0


def test_solo_el_admin_cambia_precios_costos_y_cantidades():
    ana, beto = TestClient(app), TestClient(app)
    crear(ana, "ana")
    alta(ana, "beto")
    primer_ingreso(beto, "beto")
    producto(ana)  # 5 unidades, precio 3.000

    def ficha(producto_id=1):
        with db.conexion() as con:
            return inv.obtener_producto(con, producto_id)

    antes = ficha()
    for ruta, datos in (("/productos/1/precio", {"precio_venta": "9.000", "origen": "precio"}),
                        ("/productos/1/margen", {"margen_pct": "90"}),
                        ("/productos/1/unir", {"destino_id": "1"}),
                        ("/productos/1/stock", {"cantidad": "99", "motivo": "Conteo"}),
                        ("/compras/1/editar", {"cantidad": "1", "costo_total": "1"}),
                        ("/compras/1/borrar", {})):
        assert "Solo el administrador puede cambiar precios" in beto.post(ruta, data=datos).text, ruta
    assert "Solo el administrador puede cambiar precios" in beto.get("/compras/1/editar").text
    despues = ficha()
    assert (despues["stock"], despues["precio_venta"], despues["margen_pct"]) == (5, 3000, antes["margen_pct"])

    # en pantalla: precios como texto, sin editar compras ni unir productos
    r = beto.get("/productos")
    assert 'action="/productos/1/precio"' not in r.text and "₲ 3.000" in r.text
    assert 'action="/productos/1/stock"' not in r.text and 'data-stock-id="1">5<' in r.text
    assert "/compras/1/editar" not in beto.get("/compras").text
    r = beto.get("/productos/1/editar")
    assert 'name="precio_venta"' not in r.text and 'id="buscador-unir"' not in r.text
    assert 'action="/productos/1/stock"' not in r.text
    assert "los cambia el administrador" in r.text and 'name="stock"' not in r.text

    # puede editar el resto de la ficha, pero el precio que mande no se toma
    beto.post("/productos/1/editar", data={"nombre_interno": "Molde A grande", "categoria_id": "1",
                                           "margen_pct": "300", "precio_venta": "99.999", "activo": "1"})
    p = ficha()
    assert p["nombre_interno"] == "Molde A grande" and p["precio_venta"] == 3000 and p["stock"] == 5

    # productos nuevos: sin compra ni precio, para que el administrador los complete
    r = beto.get("/productos/nuevo")
    assert 'name="cantidad"' not in r.text and 'name="precio_venta"' not in r.text and "stock 0" in r.text
    beto.post("/productos/nuevo", data={"nombre_interno": "Vela nueva", "categoria_id": "1", "proveedor": "X",
                                        "cantidad": "50", "costo_total": "100000", "precio_venta": "5000"})
    nuevo = ficha(2)
    assert nuevo["nombre_interno"] == "Vela nueva" and nuevo["stock"] == 0 and nuevo["precio_venta"] == 0
    assert nuevo["revisar"] == 1
    with db.conexion() as con:
        assert con.execute("SELECT COUNT(*) FROM compras").fetchone()[0] == 1
    beto.post("/productos/2/editar", data={"nombre_interno": "Vela nueva roja", "categoria_id": "1", "activo": "1"})
    assert ficha(2)["revisar"] == 1  # sigue esperando al administrador
    ana.post("/productos/2/editar", data={"nombre_interno": "Vela nueva roja", "categoria_id": "1",
                                          "margen_pct": "40", "precio_venta": "5.000", "activo": "1"})
    assert ficha(2)["revisar"] == 0 and ficha(2)["precio_venta"] == 5000

    # el stock solo baja con ventas, y vender lo puede cualquiera
    beto.post("/vender", data={"producto_id": ["1"], "cantidad": ["2"], "medio_pago": "Efectivo"})
    assert ficha()["stock"] == 3

    # el administrador sí puede
    r = ana.post("/productos/1/precio", data={"precio_venta": "4.000", "origen": "precio"})
    assert "precio ₲ 4.000" in r.text
    r = ana.post("/compras/1/editar", data={"fecha": "2026-06-05", "nro_factura": "", "proveedor": "Pacific SA",
                                            "cantidad": "5", "costo_total": "5000"})
    assert "Solo el administrador" not in r.text and "Compra corregida" in r.text


def test_el_admin_ajusta_el_stock_desde_la_ficha():
    ana = TestClient(app)
    crear(ana, "ana")
    producto(ana)  # 5 unidades
    r = ana.get("/productos/1/editar")
    assert 'name="stock" inputmode="decimal" value="5"' in r.text and 'name="motivo_stock"' in r.text

    r = ana.post("/productos/1/stock", data={"cantidad": "3", "motivo": "Se rompieron dos"})
    assert "ahora hay 3 en stock" in r.text
    assert "Últimos cambios de stock" in r.text and "Se rompieron dos" in r.text  # en la ficha
    r = ana.post("/productos/1/stock", data={"cantidad": "8", "motivo": "Conteo de inventario"})
    assert "ahora hay 8 en stock" in r.text
    assert "📦 Ajuste de stock" in ana.get("/compras").text
    r = ana.get("/compras/panel")  # el ajuste (+5 a ₲ 1.000) no es plata gastada
    assert "₲ 5.000" in r.text and "₲ 10.000" not in r.text
    r = ana.post("/productos/1/stock", data={"cantidad": "8", "motivo": "Conteo"})
    assert "no hay nada que cambiar" in r.text
    with db.conexion() as con:
        assert inv.stock(con, 1) == 8

    # la lista es solo para mirar, también para el administrador: se cambia desde Editar
    r = ana.get("/productos")
    for campo in ('action="/productos/1/stock"', 'action="/productos/1/precio"', 'name="cantidad"',
                  'name="precio_venta"', 'name="margen_pct"'):
        assert campo not in r.text, campo
    assert 'data-stock-id="1">8<' in r.text and "₲ 3.000" in r.text and 'action="/productos/1/activo"' in r.text
    r = ana.post("/productos/1/stock", data={"cantidad": "6", "volver": "lista"})
    assert r.url.path == "/productos" and "ahora hay 6 en stock" in r.text
    with db.conexion() as con:
        assert inv.stock(con, 1) == 6
    r = ana.get("/actividad")
    assert "Ajustó el stock" in r.text and "de 5 a 3 (-2). Motivo: Se rompieron dos" in r.text
    assert "de 3 a 8 (+5)" in r.text and "de 8 a 6 (-2). Motivo: Corrección manual" in r.text


def test_el_admin_cambia_el_stock_al_editar_el_producto():
    ana, beto = TestClient(app), TestClient(app)
    crear(ana, "ana")
    alta(ana, "beto")
    primer_ingreso(beto, "beto")
    producto(ana)  # 5 unidades
    ficha = {"nombre_interno": "Molde A", "categoria_id": "1", "margen_pct": "40",
             "precio_venta": "3.000", "activo": "1"}

    def stock():
        with db.conexion() as con:
            return inv.stock(con, 1)

    r = ana.post("/productos/1/editar", data={**ficha, "stock": "9", "stock_antes": "5",
                                              "motivo_stock": "Conteo de inventario"})
    assert "Ahora hay 9 en stock (antes 5)" in r.text and stock() == 9
    # sin tocar el número no se ajusta nada, aunque el stock haya cambiado mientras tanto
    beto.post("/vender", data={"producto_id": ["1"], "cantidad": ["2"], "medio_pago": "Efectivo"})
    r = ana.post("/productos/1/editar", data={**ficha, "stock": "9", "stock_antes": "9"})
    assert "Ahora hay" not in r.text and stock() == 7
    # un número malo no guarda nada
    r = ana.post("/productos/1/editar", data={**ficha, "nombre_interno": "Otro", "stock": "-3", "stock_antes": "7"})
    assert "menor a cero" in r.text and stock() == 7
    with db.conexion() as con:
        assert inv.obtener_producto(con, 1)["nombre_interno"] == "Molde A"
    # el usuario común no ve el campo y lo que mande no se toma
    assert 'name="stock"' not in beto.get("/productos/1/editar").text
    beto.post("/productos/1/editar", data={**ficha, "stock": "99", "stock_antes": "7"})
    assert stock() == 7
    r = ana.get("/actividad")
    assert "de 5 a 9 (+4). Motivo: Conteo de inventario" in r.text

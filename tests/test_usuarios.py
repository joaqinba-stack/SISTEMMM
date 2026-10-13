import re

from fastapi.testclient import TestClient

from app import db, usuarios
from app.main import app

CLAVE = "clave-de-prueba"


def crear(c, usuario, nombre=None, clave=CLAVE):
    return c.post("/cuenta/nueva", data={"nombre": nombre or usuario.capitalize(), "usuario": usuario,
                                         "clave": clave, "repetir": clave})


def codigo_en(r) -> str:
    return re.search(r'id="codigo">([A-Z0-9-]+)<', r.text).group(1)


def entrar(c, usuario, clave=CLAVE):
    return c.post("/login", data={"usuario": usuario, "clave": clave})


def producto(c, nombre="Molde A", cantidad="5", precio="3.000"):
    return c.post("/productos/nuevo", data={
        "nombre_interno": nombre, "categoria_id": "1", "proveedor": "Pacific", "fecha": "2026-06-05",
        "cantidad": cantidad, "costo_total": "5000", "margen_pct": "40", "precio_venta": precio})


def test_la_primera_cuenta_administra_y_las_demas_esperan_aprobacion():
    ana, beto = TestClient(app), TestClient(app)
    r = crear(ana, "ana")
    assert "sos el administrador" in r.text and re.fullmatch(r"(\w{4}-){2}\w{4}", codigo_en(r))
    assert r.headers["cache-control"] == "no-store"
    assert ana.get("/usuarios").url.path == "/usuarios"

    r = crear(beto, "beto")
    assert "espera la aprobación" in r.text
    assert beto.get("/productos").url.path == "/login"
    assert "espera la aprobación" in entrar(beto, "beto").text

    r = ana.get("/usuarios")
    assert "Esperando aprobación" in r.text and re.search(r'class="globo"[^>]*>1<', r.text)
    assert "Habilitó la cuenta de Beto" in ana.post("/usuarios/2/estado", data={"estado": "activo"}).text
    assert entrar(beto, "beto").url.path == "/"
    r = beto.get("/usuarios")  # no es administrador
    assert r.url.path == "/" and "solo para administradores" in r.text
    assert beto.get("/actividad").url.path == "/"

    ana.post("/usuarios/2/estado", data={"estado": "bloqueado"})
    assert beto.get("/productos").url.path == "/login"  # la sesión abierta se corta
    assert "está bloqueada" in entrar(beto, "beto").text


def test_no_acepta_cuentas_mal_armadas():
    c = TestClient(app)
    assert "al menos 8" in crear(c, "ana", clave="corta").text
    assert "entre 3 y 30" in crear(c, "a b").text
    r = c.post("/cuenta/nueva", data={"nombre": "Ana", "usuario": "ana", "clave": CLAVE, "repetir": CLAVE + "x"})
    assert "no son iguales" in r.text and 'value="ana"' in r.text
    crear(c, "ana")
    assert "Ya hay una cuenta" in crear(TestClient(app), "ANA").text


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
    crear(beto, "beto")
    ana.post("/usuarios/2/estado", data={"estado": "activo"})
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
    crear(beto, "beto")
    ana.post("/usuarios/2/estado", data={"estado": "activo"})
    entrar(beto, "beto")
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
    for hecho in ("Creó su cuenta", "Habilitó la cuenta", "Entró al sistema", "Creó un producto",
                  "Registró una venta", "Venta n.º 2: 1 producto(s) por ₲ 3.000"):
        assert hecho in r.text
    assert r.text.count("Registró una venta") == 2
    solo_beto = ana.get("/actividad?usuario_id=2").text
    assert "Registró una venta" in solo_beto and "Creó un producto" not in solo_beto

    ana.post("/productos/1/activo")
    ana.get("/ventas/excel")
    r = ana.get("/actividad")
    assert "Desactivó un producto" in r.text and "Descargó un Excel" in r.text

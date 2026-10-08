import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import config
from app.main import app


def cliente():
    return TestClient(app)


def test_flujo_completo_web():
    with cliente() as c:
        assert c.get("/").status_code == 200
        r = c.get("/productos/nuevo")
        assert "P-00001" in r.text and "SKU" not in r.text.split("<form")[1]

        r = c.post("/productos/nuevo", data={
            "nombre_interno": "Molde vela corazón", "marca": "Super K", "categoria_id": "1",
            "proveedor": "Super K", "nombre_proveedor": "MOLDE VELA CORAZON P J27592",
            "fecha": "2026-06-05", "nro_factura": "001-001-0133014", "cantidad": "6",
            "costo_total": "52.542", "margen_pct": "40", "precio_venta": "",
        })
        assert r.status_code == 200 and "Producto guardado" in r.text
        assert "MOL-VEL-COR-00001" in r.text and "20022001" in r.text
        assert "₲ 12.260" in r.text  # 8.757 + 40 %

        r = c.get("/compras")
        assert "001-001-0133014" in r.text and "₲ 8.757" in r.text and "₲ 52.542" in r.text

        r = c.post("/vender", data={"producto_id": "1", "cantidad": "2", "precio_unit": "25.000",
                                    "cliente": "Kari", "medio_pago": "Efectivo",
                                    "fecha": "2026-06-26"})
        assert "Venta guardada" in r.text and "Quedan 4" in r.text

        assert c.get("/api/stock").json()["stock"]["1"] == 4
        assert c.get("/barra/20022001.svg").headers["content-type"].startswith("image/svg")

        for ruta in ("/productos/excel", "/compras/excel", "/ventas/excel"):
            r = c.get(ruta)
            libro = load_workbook(io.BytesIO(r.content))
            assert libro.active.max_row == 2

        r = c.post("/productos/1/editar", data={
            "nombre_interno": "Molde vela corazón P", "categoria_id": "1", "proveedor": "Super K",
            "margen_pct": "50", "precio_venta": "13.136", "activo": "1"})
        assert "Cambios guardados" in r.text and "₲ 13.136" in r.text

        r = c.post("/vender", data={"producto_id": "1", "cantidad": "99", "precio_unit": "1"})
        assert "No alcanza el stock" in r.text


def test_pide_clave(monkeypatch):
    monkeypatch.setattr(config, "CLAVE_ACCESO", "1234")
    with cliente() as c:
        r = c.get("/productos")
        assert r.url.path == "/login"
        r = c.post("/login", data={"clave": "mal"})
        assert "no es correcta" in r.text
        r = c.post("/login", data={"clave": "1234"})
        assert r.url.path == "/"
        assert c.get("/productos").url.path == "/productos"


def test_etiquetas_codigo_de_barra():
    with cliente() as c:
        c.post("/productos/nuevo", data={"nombre_interno": "Molde vela", "categoria_id": "1",
                                         "proveedor": "Super K", "cantidad": "3",
                                         "costo_total": "30000"})
        r = c.get("/etiquetas?ids=1")
        assert 'name="c_1" value="1"' in r.text and 'data-stock="3"' in r.text
        r = c.get("/etiquetas/hoja?c_1=3&precio=1")
        assert r.text.count('class="etiqueta-impresa"') == 3 and "₲ 14.000" in r.text
        r = c.get("/etiquetas/hoja?c_1=0")
        assert r.url.path == "/etiquetas" and "al menos un producto" in r.text
        svg = c.get("/barra/20022001.svg").text
        assert "viewBox=" in svg

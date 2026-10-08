import io
import re

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader

from app import config
from app.main import app


def cliente():
    return TestClient(app)


def nuevo_producto(c, nombre="Molde vela corazón", cantidad="6", costo="52.542", **extra):
    datos = {"nombre_interno": nombre, "marca": "Super K", "categoria_id": "1",
             "proveedor": "Super K", "nombre_proveedor": "MOLDE VELA CORAZON P J27592",
             "fecha": "2026-06-05", "nro_factura": "001-001-0133014", "cantidad": cantidad,
             "costo_total": costo, "margen_pct": "40", "precio_venta": ""}
    datos.update(extra)
    return c.post("/productos/nuevo", data=datos)


def textos_pdf(contenido: bytes) -> str:
    return "\n".join(pagina.extract_text() for pagina in PdfReader(io.BytesIO(contenido)).pages)


def test_flujo_completo_web():
    with cliente() as c:
        r = c.get("/")
        assert "No hay avisos" in r.text
        r = c.get("/productos/nuevo")
        assert "P-00001" in r.text and "SKU" not in r.text.split("<form")[1]

        r = nuevo_producto(c)
        assert r.status_code == 200 and "Producto guardado" in r.text
        assert "MOL-VEL-COR-00001" in r.text and "20022001" in r.text
        assert "₲ 12.260" in r.text  # 8.757 + 40 %
        assert "Imprimir lista" not in r.text and "Cambiar precios por porcentaje" not in r.text

        r = c.get("/compras")
        assert "001-001-0133014" in r.text and "₲ 8.757" in r.text and "₲ 52.542" in r.text
        assert "Agregar compra" not in r.text and "/compras/panel" in r.text
        assert c.get("/compras/nueva").status_code in (404, 405)

        assert c.get("/api/stock").json()["stock"]["1"] == 6
        assert c.get("/barra/20022001.svg").headers["content-type"].startswith("image/svg")

        r = c.post("/productos/1/editar", data={
            "nombre_interno": "Molde vela corazón P", "categoria_id": "1", "proveedor": "Super K",
            "margen_pct": "50", "precio_venta": "13.136", "activo": "1"})
        assert "Cambios guardados" in r.text and "₲ 13.136" in r.text


def test_margen_por_producto_en_la_tabla():
    with cliente() as c:
        nuevo_producto(c, cantidad="1", costo="10000")
        r = c.post("/productos/1/margen", data={"margen_pct": "100"})
        assert "nuevo precio ₲ 20.000" in r.text
        r = c.post("/productos/1/margen", data={"margen_pct": "abc"})
        assert "porcentaje válido" in r.text


def test_venta_con_carrito_y_cliente_nuevo():
    with cliente() as c:
        nuevo_producto(c, "Molde A", cantidad="5", costo="5000", precio_venta="3.000")
        nuevo_producto(c, "Molde B", cantidad="2", costo="2000", precio_venta="2.500")
        r = c.get("/vender")
        assert 'id="datos-productos"' in r.text and "Molde A" in r.text

        nuevo = c.post("/api/clientes", json={"nombre": "Kari Personalizados", "telefono": "0981 1",
                                              "documento": "80012345-6"}).json()
        assert nuevo["texto"] == "Kari Personalizados" and "RUC/CI 80012345-6" in nuevo["detalle"]
        assert c.post("/api/clientes", json={"nombre": " "}).status_code == 400

        r = c.post("/vender", data={"producto_id": ["1", "2"], "cantidad": ["2", "1"],
                                    "precio_unit": "1", "cliente_id": str(nuevo["id"]),
                                    "medio_pago": "Efectivo", "fecha": "2026-06-26"})
        assert "Venta guardada: 2 producto(s) por ₲ 8.500" in r.text
        assert "Kari Personalizados" in r.text
        stock = c.get("/api/stock").json()["stock"]
        assert stock["1"] == 3 and stock["2"] == 1

        r = c.post("/vender", data={"producto_id": ["1", "2"], "cantidad": ["1", "5"]})
        assert "No alcanza el stock de «Molde B»" in r.text
        assert c.get("/api/stock").json()["stock"]["1"] == 3

        libro = load_workbook(io.BytesIO(c.get("/ventas/excel").content))
        assert libro.active.max_row == 3


def test_panel_de_compras_con_filtros():
    with cliente() as c:
        nuevo_producto(c, "Molde A", fecha="2026-05-10", proveedor="Pacific", costo="1000")
        nuevo_producto(c, "Molde B", fecha="2026-06-10", proveedor="Super K", costo="3000")
        r = c.get("/compras/panel")
        assert "Gasto por proveedor" in r.text and "₲ 4.000" in r.text
        r = c.get("/compras/panel?desde=2026-06-01")
        assert "Molde B" in r.text and "<b>Molde A</b>" not in r.text and "₲ 3.000" in r.text
        r = c.get("/compras/panel?producto_id=1")
        assert "<b>Molde A</b>" in r.text and "<b>Molde B</b>" not in r.text
        libro = load_workbook(io.BytesIO(c.get("/compras/excel?producto_id=2").content))
        assert libro.active.max_row == 2 and libro.active["E2"].value == "Molde B"


def test_etiquetas_pdf_por_factura_y_por_producto():
    with cliente() as c:
        nuevo_producto(c, "Molde A", cantidad="3", costo="3000")
        r = c.get("/etiquetas")
        assert "Etiquetas de una factura" in r.text and "Etiquetas de un producto" in r.text
        assert "data-copias" not in r.text

        r = c.get("/etiquetas/factura/1.pdf")
        assert r.headers["content-type"] == "application/pdf"
        assert textos_pdf(r.content).count("20022001") == 3  # una por unidad comprada

        r = c.get("/etiquetas/producto.pdf?producto_id=1&cantidad=25")
        lector = PdfReader(io.BytesIO(r.content))
        assert len(lector.pages) == 2  # 21 por hoja
        assert textos_pdf(r.content).count("20022001") == 25 and "Gs. 1.400" in textos_pdf(r.content)

        r = c.get("/etiquetas/producto.pdf?producto_id=&cantidad=2")
        assert "Elegí el producto" in r.text
        assert c.get("/etiquetas?producto_id=1").text.count('value="Molde A"') == 1


def test_notificaciones_con_badge():
    with cliente() as c:
        nuevo_producto(c, "Molde A", cantidad="1", costo="1000")
        r = c.get("/")
        assert "Queda poco stock" in r.text
        assert re.search(r'class="globo"[^>]*>1<', r.text)


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

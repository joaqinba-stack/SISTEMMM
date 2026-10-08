"""Hoja A4 de etiquetas con código de barra (3 x 7 = 21 etiquetas de 63,5 x 38,1 mm)."""
import io
import math

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from app import inventario as inv

COLUMNAS, FILAS = 3, 7
ANCHO, ALTO = 63.5 * mm, 38.1 * mm
SEPARACION = 2.5 * mm
MARGEN_X = (A4[0] - (COLUMNAS * ANCHO + (COLUMNAS - 1) * SEPARACION)) / 2
MARGEN_Y = (A4[1] - FILAS * ALTO) / 2
MAXIMO = 1000


def cantidad_etiquetas(cantidad) -> int:
    """Una etiqueta por unidad; si la cantidad tiene decimales se redondea para arriba."""
    return max(0, math.ceil(round(float(cantidad or 0), 6)))


def _precio(valor) -> str:
    # La letra estándar del PDF no tiene el símbolo ₲, por eso se usa "Gs."
    return "Gs. " + f"{round(valor or 0):,}".replace(",", ".")


def _etiqueta(c: canvas.Canvas, x: float, y: float, producto, con_precio: bool) -> None:
    """De arriba hacia abajo: nombre (hasta 2 líneas), barras, número, precio y código."""
    centro = x + ANCHO / 2
    lineas = simpleSplit(producto["nombre_interno"], "Helvetica-Bold", 8, ANCHO - 6 * mm)
    if len(lineas) > 2:
        lineas = lineas[:2]
        lineas[1] = lineas[1][: max(0, len(lineas[1]) - 1)].rstrip() + "…"
    c.setFont("Helvetica-Bold", 8)
    for i, linea in enumerate(lineas):
        c.drawCentredString(centro, y + ALTO - (4.5 + i * 3.2) * mm, linea)

    codigo = str(producto["codigo_barra"])
    alto_barras = 10 * mm if con_precio else 13 * mm
    barra = code128.Code128(codigo, barHeight=alto_barras, barWidth=0.33 * mm, quiet=False)
    if barra.width > ANCHO - 8 * mm:
        barra = code128.Code128(codigo, barHeight=alto_barras, quiet=False,
                                barWidth=0.33 * mm * (ANCHO - 8 * mm) / barra.width)
    base_barras = y + ALTO - 11 * mm - alto_barras
    barra.drawOn(c, centro - barra.width / 2, base_barras)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(centro, base_barras - 3 * mm, codigo)

    if con_precio:
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(centro, y + 4.4 * mm, _precio(producto["precio_venta"]))
    c.setFont("Helvetica", 5.5)
    c.drawCentredString(centro, y + 1.6 * mm, f"{producto['codigo']} · {producto['sku']}")


def hoja_pdf(productos: list, titulo: str, con_precio: bool = True) -> bytes:
    """productos: lista con un elemento por etiqueta (repetidos si van varias del mismo)."""
    if not productos:
        raise inv.ErrorNegocio("No hay etiquetas para imprimir.")
    if len(productos) > MAXIMO:
        raise inv.ErrorNegocio(f"Son demasiadas etiquetas. El máximo es {MAXIMO} por vez.")
    salida = io.BytesIO()
    c = canvas.Canvas(salida, pagesize=A4)
    c.setTitle(titulo)
    por_hoja = COLUMNAS * FILAS
    for n, producto in enumerate(productos):
        if n and n % por_hoja == 0:
            c.showPage()
        lugar = n % por_hoja
        fila, columna = divmod(lugar, COLUMNAS)
        x = MARGEN_X + columna * (ANCHO + SEPARACION)
        y = A4[1] - MARGEN_Y - (fila + 1) * ALTO
        _etiqueta(c, x, y, producto, con_precio)
    c.save()
    return salida.getvalue()


def etiquetas_de_factura(con, compra_id: int) -> tuple[list, str]:
    """Una etiqueta por cada unidad comprada en esa factura."""
    renglones = inv.listar_compras(con, compra_id=compra_id)
    if not renglones:
        raise inv.ErrorNegocio("Esa factura no existe.")
    lista = []
    for r in renglones:
        producto = inv.obtener_producto(con, r["producto_id"])
        lista.extend([producto] * cantidad_etiquetas(r["cantidad"]))
    nombre = renglones[0]["nro_factura"] or f"compra-{compra_id}"
    return lista, nombre

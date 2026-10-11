"""Etiquetas con código de barra: PDF para rollo térmico (una etiqueta por página), hoja A4
de 21 etiquetas y vista previa en SVG. Todo usa el mismo diseño (app/etiqueta_diseno.py)."""
import html
import io
import math

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from app import config, inventario as inv
from app.etiqueta_diseno import TAMANOS, Caja, diseno_etiqueta, precio_texto, tamano

MAXIMO = 1000
# Hoja A4 de 3 x 7 = 21 etiquetas de 63,5 x 38,1 mm
A4_COLUMNAS, A4_FILAS = 3, 7
A4_ANCHO, A4_ALTO, A4_SEPARACION = 63.5, 38.1, 2.5
A4_MARGEN_X = (A4[0] / mm - (A4_COLUMNAS * A4_ANCHO + (A4_COLUMNAS - 1) * A4_SEPARACION)) / 2
A4_MARGEN_Y = (A4[1] / mm - A4_FILAS * A4_ALTO) / 2
ALTURA_MAYUSCULA = 0.72  # alto de una mayúscula de Helvetica respecto del tamaño de letra


def tamano_elegido(con) -> str:
    """El tamaño de etiqueta elegido la última vez en la página (o el del .env)."""
    clave = inv.ajuste(con, "etiqueta_tamano", config.ETIQUETA_TAMANO)
    return clave if clave in TAMANOS else "55x28"


def cantidad_etiquetas(cantidad) -> int:
    """Una etiqueta por unidad; si la cantidad tiene decimales se redondea para arriba."""
    return max(0, math.ceil(round(float(cantidad or 0), 6)))


def _tamano_letra(alto_mm: float) -> float:
    """Tamaño de letra (en mm) para que las mayúsculas midan alto_mm."""
    return alto_mm / ALTURA_MAYUSCULA


def _ancho_texto(texto: str, letra_mm: float, negrita: bool) -> float:
    return stringWidth(texto, "Helvetica-Bold" if negrita else "Helvetica", letra_mm)


def _ajustar(texto: str, letra_mm: float, ancho_mm: float, negrita: bool) -> float:
    """Achica la letra si el texto no entra en el ancho."""
    ancho = _ancho_texto(texto, letra_mm, negrita)
    return letra_mm if ancho <= ancho_mm else letra_mm * ancho_mm / ancho


def _partir_nombre(nombre: str, letra_mm: float, ancho_mm: float, lineas: int) -> list[str]:
    palabras, renglones, actual = nombre.split(), [], ""
    for palabra in palabras:
        prueba = f"{actual} {palabra}".strip()
        if actual and _ancho_texto(prueba, letra_mm, True) > ancho_mm:
            renglones.append(actual)
            actual = palabra
        else:
            actual = prueba
    if actual:
        renglones.append(actual)
    if len(renglones) > lineas:
        renglones = renglones[:lineas]
        ultimo = renglones[-1]
        while ultimo and _ancho_texto(ultimo + "…", letra_mm, True) > ancho_mm:
            ultimo = ultimo[:-1]
        renglones[-1] = ultimo.rstrip() + "…"
    return renglones


def dibujar(pintor, producto, con_precio: bool = True) -> None:
    """Dibuja una etiqueta con cualquier «pintor» (PDF o SVG) usando el diseño del tamaño."""
    d = diseno_etiqueta(pintor.ancho, pintor.alto, str(producto["codigo_barra"]), con_precio)
    letra = _tamano_letra(d.alto_linea * 0.78)
    for i, renglon in enumerate(_partir_nombre(producto["nombre_interno"], letra, d.nombre.ancho, d.lineas_nombre)):
        caja = Caja(d.nombre.x, d.nombre.y + i * d.alto_linea, d.nombre.ancho, d.alto_linea)
        pintor.texto(caja, renglon, _ajustar(renglon, letra, caja.ancho, True), True, "centro",
                     base=caja.y + d.alto_linea * 0.82)

    pintor.barras(d.barras, d.patron, d.modulo)
    numero = str(producto["codigo_barra"])
    alinear_numero = "izquierda" if d.precio_a_la_derecha and d.precio else "centro"
    pintor.texto(d.numero, numero, _ajustar(numero, _tamano_letra(d.numero.alto * 0.95), d.numero.ancho, False),
                 False, alinear_numero)
    if d.precio:
        texto = precio_texto(producto["precio_venta"])
        pintor.texto(d.precio, texto, _ajustar(texto, _tamano_letra(d.precio.alto), d.precio.ancho, True), True,
                     "derecha" if d.precio_a_la_derecha else "centro")
    if d.codigo:
        texto = producto["codigo"]
        pintor.texto(d.codigo, texto, _ajustar(texto, _tamano_letra(d.codigo.alto), d.codigo.ancho, False),
                     False, "centro")


class PintorPdf:
    """Dibuja en un canvas de reportlab; (x0, y0) es la esquina de abajo a la izquierda en mm."""

    def __init__(self, c: canvas.Canvas, x0: float, y0: float, ancho: float, alto: float):
        self.c, self.x0, self.y0, self.ancho, self.alto = c, x0, y0, ancho, alto

    def _y(self, y_desde_arriba: float) -> float:
        return (self.y0 + self.alto - y_desde_arriba) * mm

    def texto(self, caja: Caja, texto: str, letra_mm: float, negrita: bool, alinear: str, base=None):
        self.c.setFont("Helvetica-Bold" if negrita else "Helvetica", letra_mm * mm)
        y = self._y(caja.abajo if base is None else base)
        if alinear == "izquierda":
            self.c.drawString((self.x0 + caja.x) * mm, y, texto)
        elif alinear == "derecha":
            self.c.drawRightString((self.x0 + caja.x + caja.ancho) * mm, y, texto)
        else:
            self.c.drawCentredString((self.x0 + caja.x + caja.ancho / 2) * mm, y, texto)

    def barras(self, caja: Caja, patron: str, modulo: float):
        self.c.setFillColorRGB(0, 0, 0)
        x = caja.x
        for bit in patron:
            if bit == "1":
                self.c.rect((self.x0 + x) * mm, self._y(caja.abajo), modulo * mm, caja.alto * mm,
                            stroke=0, fill=1)
            x += modulo


class PintorSvg:
    def __init__(self, ancho: float, alto: float):
        self.ancho, self.alto, self.partes = ancho, alto, []

    def texto(self, caja: Caja, texto: str, letra_mm: float, negrita: bool, alinear: str, base=None):
        x = {"izquierda": caja.x, "derecha": caja.x + caja.ancho}.get(alinear, caja.x + caja.ancho / 2)
        ancla = {"izquierda": "start", "derecha": "end"}.get(alinear, "middle")
        self.partes.append(
            f'<text x="{x:.2f}" y="{(caja.abajo if base is None else base):.2f}" font-size="{letra_mm:.2f}" '
            f'text-anchor="{ancla}" font-weight="{"700" if negrita else "400"}">{html.escape(texto)}</text>')

    def barras(self, caja: Caja, patron: str, modulo: float):
        x = caja.x
        for bit in patron:
            if bit == "1":
                self.partes.append(f'<rect x="{x:.3f}" y="{caja.y:.2f}" width="{modulo:.3f}" height="{caja.alto:.2f}"/>')
            x += modulo

    def svg(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.ancho} {self.alto}" '
                f'width="{self.ancho}mm" height="{self.alto}mm" role="img" aria-label="Vista previa de la etiqueta">'
                f'<rect width="100%" height="100%" fill="#fff"/>'
                f'<g fill="#000" font-family="Helvetica, Arial, sans-serif">{"".join(self.partes)}</g></svg>')


def _controlar(productos: list) -> None:
    if not productos:
        raise inv.ErrorNegocio("No hay etiquetas para imprimir.")
    if len(productos) > MAXIMO:
        raise inv.ErrorNegocio(f"Son demasiadas etiquetas. El máximo es {MAXIMO} por vez.")


def rollo_pdf(productos: list, tamano_clave: str, titulo: str, con_precio: bool = True) -> bytes:
    """Una etiqueta por página, del tamaño exacto del rollo (para imprimir con el driver)."""
    _controlar(productos)
    ancho, alto = tamano(tamano_clave)
    salida = io.BytesIO()
    c = canvas.Canvas(salida, pagesize=(ancho * mm, alto * mm))
    c.setTitle(titulo)
    for producto in productos:
        dibujar(PintorPdf(c, 0, 0, ancho, alto), producto, con_precio)
        c.showPage()
    c.save()
    return salida.getvalue()


def hoja_pdf(productos: list, titulo: str, con_precio: bool = True) -> bytes:
    """Hoja A4 de 21 etiquetas (para impresora común)."""
    _controlar(productos)
    salida = io.BytesIO()
    c = canvas.Canvas(salida, pagesize=A4)
    c.setTitle(titulo)
    por_hoja = A4_COLUMNAS * A4_FILAS
    for n, producto in enumerate(productos):
        if n and n % por_hoja == 0:
            c.showPage()
        fila, columna = divmod(n % por_hoja, A4_COLUMNAS)
        x0 = A4_MARGEN_X + columna * (A4_ANCHO + A4_SEPARACION)
        y0 = A4[1] / mm - A4_MARGEN_Y - (fila + 1) * A4_ALTO
        dibujar(PintorPdf(c, x0, y0, A4_ANCHO, A4_ALTO), producto, con_precio)
    c.save()
    return salida.getvalue()


def vista_svg(producto, tamano_clave: str, con_precio: bool = True) -> str:
    ancho, alto = tamano(tamano_clave)
    pintor = PintorSvg(ancho, alto)
    dibujar(pintor, producto, con_precio)
    return pintor.svg()


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

"""Diseño de una etiqueta con código de barra para cualquier tamaño de rollo.

Todas las medidas están en milímetros, con el origen arriba a la izquierda (y crece hacia
abajo). El mismo diseño lo usan el PDF, la vista previa en pantalla y la impresora térmica.
"""
from dataclasses import dataclass
from functools import lru_cache

import barcode

# Tamaños de etiqueta que se pueden elegir (ancho x alto en mm).
TAMANOS = {
    "55x28": (55, 28),
    "50x30": (50, 30),
    "60x40": (60, 40),
    "40x40": (40, 40),
    "55x45": (55, 45),
}
SEPARACION_ZONA = 2.0          # espacio en blanco mínimo a cada lado de las barras (mm)
MODULOS_MM = (0.375, 0.25, 0.125)  # 3, 2 o 1 puntos de una impresora de 203 dpi


@dataclass
class Caja:
    x: float
    y: float
    ancho: float
    alto: float

    @property
    def abajo(self) -> float:
        return self.y + self.alto


@dataclass
class Diseno:
    ancho: float
    alto: float
    margen: float
    lineas_nombre: int
    nombre: Caja
    alto_linea: float
    barras: Caja
    modulo: float
    patron: str
    numero: Caja
    precio: Caja | None
    precio_a_la_derecha: bool
    codigo: Caja | None

    def cajas(self) -> list[Caja]:
        return [c for c in (self.nombre, self.barras, self.numero, self.precio, self.codigo) if c]


def tamano(clave: str) -> tuple[float, float]:
    return TAMANOS.get(clave, TAMANOS["55x28"])


@lru_cache(maxsize=4096)
def patron_barras(codigo: str) -> str:
    """Barras y espacios del Code128 como texto de unos y ceros (1 = barra)."""
    return barcode.get("code128", codigo).build()[0]


def diseno_etiqueta(ancho: float, alto: float, codigo_barra: str, con_precio: bool = True) -> Diseno:
    corta = alto < 32
    m = 1.5 if corta else 2.0
    lineas = 1 if corta else (3 if (alto >= 44 or ancho <= alto) else 2)
    alto_linea = min(max(alto * 0.11, 3.0), 4.4)
    nombre = Caja(m, m, ancho - 2 * m, lineas * alto_linea)
    arriba = nombre.abajo + 0.8

    abajo = alto - m
    codigo = None
    if not corta:
        codigo = Caja(m, abajo - 1.8, ancho - 2 * m, 1.8)
        abajo = codigo.y - 0.6

    alto_numero = min(max(alto * 0.07, 1.9), 2.6)
    alto_precio = min(max(alto * 0.12, 3.2), 5.5)

    patron = patron_barras(codigo_barra)
    disponible_ancho = ancho - 2 * m - 2 * SEPARACION_ZONA
    modulo = next((mod for mod in MODULOS_MM if len(patron) * mod <= disponible_ancho), MODULOS_MM[-1])
    ancho_barras = len(patron) * modulo
    x_barras = (ancho - ancho_barras) / 2

    precio = None
    if corta:
        fila = max(alto_precio if con_precio else 0, alto_numero)
        if con_precio:
            numero = Caja(x_barras, abajo - alto_numero, min(ancho_barras, ancho / 2 - x_barras), alto_numero)
        else:
            numero = Caja(m, abajo - alto_numero, ancho - 2 * m, alto_numero)
        if con_precio:
            precio = Caja(ancho / 2, abajo - alto_precio, ancho / 2 - m, alto_precio)
        abajo -= fila + 0.5
        espacio = abajo - arriba
        alto_barras = min(espacio, alto * (0.40 if con_precio else 0.45))
        y_barras = arriba + (espacio - alto_barras) / 2
    else:
        if con_precio:
            precio = Caja(m, abajo - alto_precio, ancho - 2 * m, alto_precio)
            abajo = precio.y - 0.8
        espacio = abajo - arriba - alto_numero - 0.4
        alto_barras = min(espacio, alto * (0.38 if con_precio else 0.45))
        y_barras = arriba + (espacio - alto_barras) / 2
        numero = Caja(m, y_barras + alto_barras + 0.4, ancho - 2 * m, alto_numero)

    return Diseno(
        ancho=ancho, alto=alto, margen=m, lineas_nombre=lineas, nombre=nombre, alto_linea=alto_linea,
        barras=Caja(x_barras, y_barras, ancho_barras, alto_barras), modulo=modulo, patron=patron,
        numero=numero, precio=precio, precio_a_la_derecha=corta, codigo=codigo,
    )


def precio_texto(valor) -> str:
    # Las letras de la impresora y del PDF no tienen el símbolo ₲, por eso se usa "Gs."
    return "Gs. " + f"{round(valor or 0):,}".replace(",", ".")

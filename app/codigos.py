"""Generación automática de SKU, código de producto y código de barra."""
import re
import sqlite3
import unicodedata

PALABRAS_VACIAS = {
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "PARA", "CON", "SIN", "Y", "X", "EN",
    "A", "AL", "POR", "UN", "UNA", "TIPO",
}


def sin_acentos(texto: str) -> str:
    texto = texto.replace("ñ", "n").replace("Ñ", "N")
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def base_sku(nombre: str) -> str:
    """'Balanza Digital 10kg' -> 'BAL-DIG-10KG' (mismo estilo que el Excel)."""
    palabras = re.split(r"[^A-Z0-9]+", sin_acentos(nombre).upper())
    partes = []
    for palabra in palabras:
        if not palabra or palabra in PALABRAS_VACIAS:
            continue
        if any(c.isdigit() for c in palabra):
            partes.append(palabra[:6])  # medidas como 10KG, 1000W, 750ML se dejan enteras
        else:
            partes.append(palabra[:3])
        if len(partes) == 4:
            break
    return "-".join(partes) or "PRO"


def nuevo_sku(con: sqlite3.Connection, nombre: str) -> str:
    base = base_sku(nombre)
    filas = con.execute("SELECT sku FROM productos WHERE sku LIKE ?", (base + "-%",)).fetchall()
    mayor = 0
    for (sku,) in filas:
        resto = sku[len(base) + 1 :]
        if resto.isdigit():
            mayor = max(mayor, int(resto))
    return f"{base}-{mayor + 1:05d}"


def nuevo_codigo_producto(con: sqlite3.Connection) -> str:
    fila = con.execute(
        "SELECT MAX(CAST(SUBSTR(codigo, 3) AS INTEGER)) FROM productos WHERE codigo LIKE 'P-%'"
    ).fetchone()
    return f"P-{(fila[0] or 0) + 1:05d}"


def nuevo_codigo_barra(con: sqlite3.Connection, categoria_id: int) -> str:
    """Siguiente número de la categoría: Vela 20022001, 20022002, ..."""
    base = con.execute(
        "SELECT base_barra FROM categorias WHERE id = ?", (categoria_id,)
    ).fetchone()[0]
    tope = base + 999_999
    fila = con.execute(
        """SELECT MAX(CAST(codigo_barra AS INTEGER)) FROM productos
           WHERE CAST(codigo_barra AS INTEGER) > ? AND CAST(codigo_barra AS INTEGER) <= ?""",
        (base, tope),
    ).fetchone()
    siguiente = max(fila[0] or base, base) + 1
    while con.execute(
        "SELECT 1 FROM productos WHERE codigo_barra = ?", (str(siguiente),)
    ).fetchone():
        siguiente += 1
    return str(siguiente)

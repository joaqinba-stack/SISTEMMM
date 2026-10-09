"""Generación automática del código de producto y del código de barra."""
import sqlite3
import unicodedata


def sin_acentos(texto: str) -> str:
    texto = texto.replace("ñ", "n").replace("Ñ", "N")
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


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

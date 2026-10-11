"""Generación automática del código de producto (también es su código de barra)."""
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

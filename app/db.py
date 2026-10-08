"""Base de datos SQLite: creación de tablas y conexión."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app import config

# Categorías fijas. El prefijo continúa la numeración de códigos de barra del Excel
# (Varios 1001-1xxx, Vela 2002-2xxx, Jabón 3003-3xxx, Yeso 4004-4xxx, Resina 5005-5xxx).
CATEGORIAS = [
    ("Vela", 20022000),
    ("Jabón", 30033000),
    ("Yeso", 40044000),
    ("Resina", 50055000),
    ("Varios", 10011000),
]

ESQUEMA = """
CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,
    base_barra INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proveedores (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    clave TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL UNIQUE,
    sku TEXT NOT NULL UNIQUE,
    codigo_barra TEXT NOT NULL UNIQUE,
    nombre_interno TEXT NOT NULL,
    nombre_proveedor TEXT NOT NULL DEFAULT '',
    marca TEXT NOT NULL DEFAULT '',
    categoria_id INTEGER NOT NULL REFERENCES categorias(id),
    proveedor_id INTEGER REFERENCES proveedores(id),
    imagen TEXT,
    margen_pct REAL NOT NULL DEFAULT 40,
    precio_venta INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    revisar INTEGER NOT NULL DEFAULT 0,
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    nro_factura TEXT NOT NULL DEFAULT '',
    proveedor_id INTEGER REFERENCES proveedores(id),
    foto_factura TEXT,
    origen TEXT NOT NULL DEFAULT 'web',
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Cada renglón de una compra es un "lote". Las ventas descuentan de los lotes
-- más viejos primero (FIFO). El stock es la suma de cantidad_restante.
CREATE TABLE IF NOT EXISTS lotes (
    id INTEGER PRIMARY KEY,
    compra_id INTEGER NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    fecha TEXT NOT NULL,
    cantidad REAL NOT NULL,
    costo_total REAL NOT NULL,
    costo_unitario REAL NOT NULL,
    cantidad_restante REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    telefono TEXT NOT NULL DEFAULT '',
    documento TEXT NOT NULL DEFAULT '',
    clave TEXT NOT NULL UNIQUE,
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS ventas (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    cliente TEXT NOT NULL DEFAULT '',
    medio_pago TEXT NOT NULL DEFAULT '',
    cliente_id INTEGER REFERENCES clientes(id),
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS venta_items (
    id INTEGER PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad REAL NOT NULL,
    precio_unit REAL NOT NULL,
    costo_fifo REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS consumos (
    id INTEGER PRIMARY KEY,
    venta_item_id INTEGER NOT NULL REFERENCES venta_items(id) ON DELETE CASCADE,
    lote_id INTEGER NOT NULL REFERENCES lotes(id),
    cantidad REAL NOT NULL,
    costo_unitario REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_lotes_producto ON lotes(producto_id, fecha, id);
CREATE INDEX IF NOT EXISTS ix_lotes_compra ON lotes(compra_id);
"""


def _conectar(ruta: Path) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


@contextmanager
def conexion():
    """Abre una conexión; confirma los cambios si todo salió bien, si no los deshace."""
    con = _conectar(config.DB_PATH)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inicializar() -> None:
    with conexion() as con:
        con.executescript(ESQUEMA)
        # Bases creadas antes de existir la tabla de clientes.
        columnas = {fila[1] for fila in con.execute("PRAGMA table_info(ventas)")}
        if "cliente_id" not in columnas:
            con.execute("ALTER TABLE ventas ADD COLUMN cliente_id INTEGER REFERENCES clientes(id)")
        for nombre, base in CATEGORIAS:
            con.execute(
                "INSERT OR IGNORE INTO categorias (nombre, base_barra) VALUES (?, ?)",
                (nombre, base),
            )
    config.ARCHIVOS_DIR.mkdir(parents=True, exist_ok=True)

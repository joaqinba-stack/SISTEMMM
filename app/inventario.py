"""Reglas del negocio: productos, compras (lotes), ventas FIFO y precios."""
import difflib
import re
import sqlite3
from datetime import date, datetime

from app import codigos, config


class ErrorNegocio(Exception):
    """Error con un mensaje claro para mostrarle a la persona."""


# ---------------------------------------------------------------- formatos

def guaranies(valor) -> str:
    if valor is None:
        return "—"
    return "₲ " + f"{round(valor):,}".replace(",", ".")


def cantidad_txt(valor) -> str:
    if valor is None:
        return "0"
    valor = round(float(valor), 3)
    if valor == int(valor):
        return f"{int(valor):,}".replace(",", ".")
    return f"{valor:,.3f}".rstrip("0").replace(",", "X").replace(".", ",").replace("X", ".")


def fecha_txt(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


def leer_numero(texto, por_defecto=None):
    """Convierte lo que escribe la persona en número: '225.000' -> 225000, '2,5' -> 2.5."""
    if texto is None:
        return por_defecto
    if isinstance(texto, (int, float)):
        return float(texto)
    limpio = re.sub(r"[^\d,.\-]", "", str(texto))
    if not limpio:
        return por_defecto
    if "," in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif limpio.count(".") >= 1:
        # En Paraguay el punto separa miles: 1.250.000. Solo es decimal si hay 1 o 2 cifras al final.
        partes = limpio.split(".")
        if len(partes) == 2 and len(partes[1]) in (1, 2):
            pass
        else:
            limpio = limpio.replace(".", "")
    try:
        return float(limpio)
    except ValueError:
        return por_defecto


def leer_fecha(texto) -> str:
    """Acepta 'AAAA-MM-DD', 'DD/MM/AAAA' o datetime. Devuelve 'AAAA-MM-DD'."""
    if isinstance(texto, datetime):
        return texto.date().isoformat()
    if isinstance(texto, date):
        return texto.isoformat()
    texto = (str(texto or "")).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto[:10], formato).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", codigos.sin_acentos(texto or "").lower()).strip()


# ---------------------------------------------------------------- catálogos

def categorias(con: sqlite3.Connection):
    return con.execute("SELECT id, nombre FROM categorias ORDER BY id").fetchall()


def categoria_id(con: sqlite3.Connection, nombre: str) -> int:
    clave = normalizar(nombre)
    for fila in categorias(con):
        if normalizar(fila["nombre"]) == clave or normalizar(fila["nombre"]) in clave:
            return fila["id"]
    return con.execute("SELECT id FROM categorias WHERE nombre = 'Varios'").fetchone()[0]


def proveedor_id(con: sqlite3.Connection, nombre: str) -> int | None:
    nombre = re.sub(r"\s+", " ", (nombre or "")).strip()
    if not nombre:
        return None
    clave = normalizar(nombre)
    fila = con.execute("SELECT id FROM proveedores WHERE clave = ?", (clave,)).fetchone()
    if fila:
        return fila[0]
    return con.execute(
        "INSERT INTO proveedores (nombre, clave) VALUES (?, ?)", (nombre, clave)
    ).lastrowid


def proveedores(con: sqlite3.Connection):
    return con.execute("SELECT id, nombre FROM proveedores ORDER BY nombre").fetchall()


# ---------------------------------------------------------------- precios y stock

def precio_sugerido(costo_unitario: float, margen_pct: float) -> int:
    return int(round(costo_unitario * (1 + margen_pct / 100)))


def stock(con: sqlite3.Connection, producto_id: int) -> float:
    fila = con.execute(
        "SELECT COALESCE(SUM(cantidad_restante), 0) FROM lotes WHERE producto_id = ?",
        (producto_id,),
    ).fetchone()
    return round(fila[0], 6)


def costo_referencia(con: sqlite3.Connection, producto_id: int) -> float:
    """Costo unitario de la última compra (sirve para calcular el precio de venta)."""
    fila = con.execute(
        """SELECT costo_unitario FROM lotes WHERE producto_id = ? AND cantidad > 0
           ORDER BY fecha DESC, id DESC LIMIT 1""",
        (producto_id,),
    ).fetchone()
    return fila[0] if fila else 0.0


# ---------------------------------------------------------------- productos

def crear_producto(con: sqlite3.Connection, datos: dict) -> dict:
    """Crea el producto con sus códigos automáticos y, si trae cantidad, su primera compra."""
    nombre = (datos.get("nombre_interno") or "").strip()
    if not nombre:
        raise ErrorNegocio("Falta escribir el nombre del producto.")
    cat_id = datos.get("categoria_id") or categoria_id(con, datos.get("categoria", "Varios"))
    prov_id = proveedor_id(con, datos.get("proveedor", ""))
    cantidad = leer_numero(datos.get("cantidad"), 0) or 0
    costo_total = leer_numero(datos.get("costo_total"), 0) or 0
    if cantidad < 0 or costo_total < 0:
        raise ErrorNegocio("La cantidad y el costo no pueden ser negativos.")
    costo_unit = costo_total / cantidad if cantidad else leer_numero(datos.get("costo_unitario"), 0) or 0
    margen = leer_numero(datos.get("margen_pct"), config.MARGEN_POR_DEFECTO)
    precio = leer_numero(datos.get("precio_venta"))
    if not precio:
        precio = precio_sugerido(costo_unit, margen)

    codigo = datos.get("codigo") or codigos.nuevo_codigo_producto(con)
    sku = datos.get("sku") or codigos.nuevo_sku(con, nombre)
    if con.execute("SELECT 1 FROM productos WHERE sku = ?", (sku,)).fetchone():
        sku = codigos.nuevo_sku(con, nombre)
    barra = str(datos.get("codigo_barra") or "").strip() or codigos.nuevo_codigo_barra(con, cat_id)
    if con.execute("SELECT 1 FROM productos WHERE codigo_barra = ?", (barra,)).fetchone():
        barra = codigos.nuevo_codigo_barra(con, cat_id)

    producto_id = con.execute(
        """INSERT INTO productos (codigo, sku, codigo_barra, nombre_interno, nombre_proveedor,
               marca, categoria_id, proveedor_id, imagen, margen_pct, precio_venta, activo, revisar)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            codigo, sku, barra, nombre,
            (datos.get("nombre_proveedor") or nombre).strip(),
            (datos.get("marca") or "").strip(),
            cat_id, prov_id, datos.get("imagen"),
            margen, int(round(precio)),
            1 if datos.get("activo", True) else 0,
            1 if datos.get("revisar") else 0,
        ),
    ).lastrowid

    if cantidad > 0:
        registrar_compra(
            con,
            fecha=datos.get("fecha"),
            nro_factura=datos.get("nro_factura", ""),
            proveedor=datos.get("proveedor", ""),
            items=[{"producto_id": producto_id, "cantidad": cantidad, "costo_total": costo_total}],
            foto=datos.get("foto_factura"),
            origen=datos.get("origen", "web"),
        )
    return {"id": producto_id, "codigo": codigo, "sku": sku, "codigo_barra": barra}


def actualizar_producto(con: sqlite3.Connection, producto_id: int, datos: dict) -> None:
    actual = con.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if not actual:
        raise ErrorNegocio("Ese producto no existe.")
    nombre = (datos.get("nombre_interno") or "").strip()
    if not nombre:
        raise ErrorNegocio("Falta escribir el nombre del producto.")
    margen = leer_numero(datos.get("margen_pct"), actual["margen_pct"])
    precio = leer_numero(datos.get("precio_venta"), actual["precio_venta"])
    con.execute(
        """UPDATE productos SET nombre_interno = ?, nombre_proveedor = ?, marca = ?,
               categoria_id = ?, proveedor_id = ?, imagen = ?, margen_pct = ?, precio_venta = ?,
               activo = ?, revisar = 0
           WHERE id = ?""",
        (
            nombre,
            (datos.get("nombre_proveedor") or "").strip(),
            (datos.get("marca") or "").strip(),
            int(datos.get("categoria_id") or actual["categoria_id"]),
            proveedor_id(con, datos.get("proveedor", "")),
            datos.get("imagen") or actual["imagen"],
            margen,
            int(round(precio or 0)),
            1 if datos.get("activo") else 0,
            producto_id,
        ),
    )


def fijar_margen(con: sqlite3.Connection, producto_id: int, margen_pct) -> int:
    """Cambia el % de ganancia de un producto y recalcula su precio de venta."""
    margen_pct = leer_numero(margen_pct)
    if margen_pct is None or margen_pct < 0:
        raise ErrorNegocio("Escribí un porcentaje válido (por ejemplo 40).")
    if not con.execute("SELECT 1 FROM productos WHERE id = ?", (producto_id,)).fetchone():
        raise ErrorNegocio("Ese producto no existe.")
    costo = costo_referencia(con, producto_id)
    if costo <= 0:
        raise ErrorNegocio("Este producto todavía no tiene costo de compra; "
                           "cambiá el precio a mano en Editar.")
    precio = precio_sugerido(costo, margen_pct)
    con.execute("UPDATE productos SET margen_pct = ?, precio_venta = ? WHERE id = ?",
                (margen_pct, precio, producto_id))
    return precio


def buscar_coincidencia(con: sqlite3.Connection, descripcion: str, prov_id: int | None):
    """Busca un producto parecido a la descripción de la factura. Devuelve la fila o None."""
    objetivo = normalizar(descripcion)
    if not objetivo:
        return None
    mejor, puntaje = None, 0.0
    for fila in con.execute("SELECT * FROM productos").fetchall():
        for candidato in (fila["nombre_proveedor"], fila["nombre_interno"], fila["sku"]):
            texto = normalizar(candidato)
            if not texto:
                continue
            p = 1.0 if texto == objetivo else difflib.SequenceMatcher(None, objetivo, texto).ratio()
            if prov_id and fila["proveedor_id"] == prov_id:
                p += 0.05  # pequeño empujón si es del mismo proveedor
            if p > puntaje:
                mejor, puntaje = fila, p
    umbral = 0.85 if prov_id else 0.92
    return mejor if puntaje >= umbral else None


# ---------------------------------------------------------------- compras

def compra_existente(con: sqlite3.Connection, prov_id: int | None, nro_factura: str):
    if not nro_factura:
        return None
    return con.execute(
        "SELECT id FROM compras WHERE nro_factura = ? AND proveedor_id IS ?",
        (nro_factura.strip(), prov_id),
    ).fetchone()


def registrar_compra(con, fecha, nro_factura, proveedor, items, foto=None, origen="web") -> int:
    """items: [{producto_id, cantidad, costo_total, (opcional) cantidad_restante}]"""
    if not items:
        raise ErrorNegocio("La compra no tiene productos.")
    fecha = leer_fecha(fecha)
    compra_id = con.execute(
        """INSERT INTO compras (fecha, nro_factura, proveedor_id, foto_factura, origen)
           VALUES (?, ?, ?, ?, ?)""",
        (fecha, (nro_factura or "").strip(), proveedor_id(con, proveedor), foto, origen),
    ).lastrowid
    for item in items:
        cantidad = leer_numero(item.get("cantidad"), 0) or 0
        costo_total = leer_numero(item.get("costo_total"), 0) or 0
        if cantidad <= 0:
            raise ErrorNegocio("La cantidad comprada tiene que ser mayor a cero.")
        restante = item.get("cantidad_restante", cantidad)
        con.execute(
            """INSERT INTO lotes (compra_id, producto_id, fecha, cantidad, costo_total,
                   costo_unitario, cantidad_restante)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (compra_id, item["producto_id"], fecha, cantidad, costo_total,
             costo_total / cantidad, restante),
        )
    return compra_id


def editar_lote(con, lote_id: int, datos: dict) -> None:
    lote = con.execute("SELECT * FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    if not lote:
        raise ErrorNegocio("Esa compra no existe.")
    fecha = leer_fecha(datos.get("fecha"))
    con.execute(
        "UPDATE compras SET fecha = ?, nro_factura = ?, proveedor_id = ? WHERE id = ?",
        (fecha, (datos.get("nro_factura") or "").strip(),
         proveedor_id(con, datos.get("proveedor", "")), lote["compra_id"]),
    )
    con.execute("UPDATE lotes SET fecha = ? WHERE compra_id = ?", (fecha, lote["compra_id"]))

    cantidad = leer_numero(datos.get("cantidad"), lote["cantidad"])
    costo_total = leer_numero(datos.get("costo_total"), lote["costo_total"])
    if cantidad <= 0:
        raise ErrorNegocio("La cantidad tiene que ser mayor a cero.")
    vendido = round(lote["cantidad"] - lote["cantidad_restante"], 6)
    if vendido > 0 and (cantidad != lote["cantidad"] or costo_total != lote["costo_total"]):
        raise ErrorNegocio(
            f"De esta compra ya se vendieron {cantidad_txt(vendido)} unidades, "
            "por eso no se puede cambiar la cantidad ni el costo. "
            "Sí se pueden cambiar la fecha, la factura y el proveedor."
        )
    con.execute(
        """UPDATE lotes SET cantidad = ?, costo_total = ?, costo_unitario = ?,
               cantidad_restante = ? WHERE id = ?""",
        (cantidad, costo_total, costo_total / cantidad, cantidad, lote_id),
    )


def borrar_lote(con, lote_id: int) -> None:
    lote = con.execute("SELECT * FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    if not lote:
        raise ErrorNegocio("Esa compra no existe.")
    if con.execute("SELECT 1 FROM consumos WHERE lote_id = ?", (lote_id,)).fetchone():
        raise ErrorNegocio("No se puede borrar: ya hay ventas que salieron de esta compra.")
    con.execute("DELETE FROM lotes WHERE id = ?", (lote_id,))
    if not con.execute("SELECT 1 FROM lotes WHERE compra_id = ?", (lote["compra_id"],)).fetchone():
        con.execute("DELETE FROM compras WHERE id = ?", (lote["compra_id"],))


# ---------------------------------------------------------------- ventas (FIFO)

def registrar_venta(con, items, cliente_id=None, medio_pago="", fecha=None) -> dict:
    """Guarda una venta con uno o más productos. El precio sale siempre de la ficha del
    producto. Si a algún producto no le alcanza el stock no se guarda nada. Cada producto
    descuenta del lote más viejo primero (FIFO) y se calcula el costo y la ganancia reales."""
    pedidos: dict[int, float] = {}
    for item in items or []:
        cantidad = leer_numero(item.get("cantidad"), 0) or 0
        if cantidad <= 0:
            raise ErrorNegocio("La cantidad vendida tiene que ser mayor a cero.")
        producto_id = int(item["producto_id"])
        pedidos[producto_id] = round(pedidos.get(producto_id, 0) + cantidad, 6)
    if not pedidos:
        raise ErrorNegocio("Agregá al menos un producto a la venta.")
    if cliente_id and not con.execute("SELECT 1 FROM clientes WHERE id = ?", (cliente_id,)).fetchone():
        raise ErrorNegocio("Ese cliente no existe.")

    productos = {}
    for producto_id, cantidad in pedidos.items():
        producto = con.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if not producto:
            raise ErrorNegocio("Uno de los productos no existe.")
        disponible = stock(con, producto_id)
        if cantidad > disponible + 1e-9:
            raise ErrorNegocio(
                f"No alcanza el stock de «{producto['nombre_interno']}»: "
                f"hay {cantidad_txt(disponible)} y se quieren vender {cantidad_txt(cantidad)}."
            )
        productos[producto_id] = producto

    venta_id = con.execute(
        "INSERT INTO ventas (fecha, cliente_id, medio_pago) VALUES (?, ?, ?)",
        (leer_fecha(fecha), cliente_id or None, medio_pago or ""),
    ).lastrowid
    total = costo_total = 0.0
    for producto_id, cantidad in pedidos.items():
        precio = productos[producto_id]["precio_venta"]
        costo = _vender_item(con, venta_id, producto_id, cantidad, precio)
        total += cantidad * precio
        costo_total += costo
    return {
        "venta_id": venta_id,
        "total": total,
        "costo": costo_total,
        "ganancia": total - costo_total,
        "productos": len(pedidos),
    }


def _vender_item(con, venta_id: int, producto_id: int, cantidad: float, precio: float) -> float:
    item_id = con.execute(
        """INSERT INTO venta_items (venta_id, producto_id, cantidad, precio_unit, costo_fifo)
           VALUES (?, ?, ?, ?, 0)""",
        (venta_id, producto_id, cantidad, precio),
    ).lastrowid
    falta, costo = cantidad, 0.0
    lotes = con.execute(
        """SELECT id, cantidad_restante, costo_unitario FROM lotes
           WHERE producto_id = ? AND cantidad_restante > 0 ORDER BY fecha, id""",
        (producto_id,),
    ).fetchall()
    for lote in lotes:
        if falta <= 1e-9:
            break
        toma = min(falta, lote["cantidad_restante"])
        con.execute(
            "UPDATE lotes SET cantidad_restante = ROUND(cantidad_restante - ?, 6) WHERE id = ?",
            (toma, lote["id"]),
        )
        con.execute(
            "INSERT INTO consumos (venta_item_id, lote_id, cantidad, costo_unitario) VALUES (?, ?, ?, ?)",
            (item_id, lote["id"], toma, lote["costo_unitario"]),
        )
        costo += toma * lote["costo_unitario"]
        falta = round(falta - toma, 6)
    con.execute("UPDATE venta_items SET costo_fifo = ? WHERE id = ?", (costo, item_id))
    return costo


# ---------------------------------------------------------------- clientes

def _clave_cliente(nombre: str, documento: str) -> str:
    return f"{normalizar(nombre)}|{re.sub(r'[^0-9a-z]', '', normalizar(documento))}"


def crear_cliente(con, nombre: str, telefono: str = "", documento: str = "") -> dict:
    """Crea el cliente, o devuelve el que ya existe con el mismo nombre y documento."""
    nombre = re.sub(r"\s+", " ", nombre or "").strip()
    if not nombre:
        raise ErrorNegocio("Falta escribir el nombre del cliente.")
    clave = _clave_cliente(nombre, documento)
    fila = con.execute("SELECT * FROM clientes WHERE clave = ?", (clave,)).fetchone()
    if not fila:
        cliente_id = con.execute(
            "INSERT INTO clientes (nombre, telefono, documento, clave) VALUES (?, ?, ?, ?)",
            (nombre, (telefono or "").strip(), (documento or "").strip(), clave),
        ).lastrowid
        fila = con.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    return dict(fila)


def listar_clientes(con):
    return con.execute(
        "SELECT id, nombre, telefono, documento FROM clientes ORDER BY nombre COLLATE NOCASE"
    ).fetchall()


def buscar_clientes(con, texto: str):
    q = normalizar(texto)
    return [c for c in listar_clientes(con)
            if q in normalizar(f"{c['nombre']} {c['telefono']} {c['documento']}")]


# ---------------------------------------------------------------- avisos

def notificaciones(con) -> dict:
    """Lo que hay que atender: productos con poco stock y productos nuevos sin revisar."""
    activos = listar_productos(con, solo_activos=True)
    return {
        "poco_stock": [p for p in activos if p["stock"] <= config.STOCK_BAJO],
        "revisar": [p for p in activos if p["revisar"]],
    }


# ---------------------------------------------------------------- listados

SQL_PRODUCTOS = """
SELECT p.*, c.nombre AS categoria, COALESCE(pr.nombre, '') AS proveedor,
       COALESCE((SELECT SUM(l.cantidad_restante) FROM lotes l WHERE l.producto_id = p.id), 0) AS stock,
       COALESCE((SELECT l.costo_unitario FROM lotes l WHERE l.producto_id = p.id AND l.cantidad > 0
                 ORDER BY l.fecha DESC, l.id DESC LIMIT 1), 0) AS costo_ultimo
FROM productos p
JOIN categorias c ON c.id = p.categoria_id
LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
"""


def listar_productos(con, solo_activos=False):
    sql = SQL_PRODUCTOS + (" WHERE p.activo = 1" if solo_activos else "")
    return con.execute(sql + " ORDER BY p.nombre_interno COLLATE NOCASE").fetchall()


def obtener_producto(con, producto_id: int):
    return con.execute(SQL_PRODUCTOS + " WHERE p.id = ?", (producto_id,)).fetchone()


SQL_COMPRAS = """
SELECT l.id AS lote_id, l.cantidad, l.costo_total, l.costo_unitario, l.cantidad_restante,
       c.id AS compra_id, c.fecha, c.nro_factura, c.foto_factura, c.origen,
       COALESCE(pr.nombre, '') AS proveedor,
       p.id AS producto_id, p.codigo, p.nombre_interno, p.nombre_proveedor
FROM lotes l
JOIN compras c ON c.id = l.compra_id
JOIN productos p ON p.id = l.producto_id
LEFT JOIN proveedores pr ON pr.id = c.proveedor_id
"""


def listar_compras(con, proveedor_id=None, producto_id=None, desde=None, hasta=None,
                   compra_id=None):
    condiciones, params = [], []
    if proveedor_id:
        condiciones.append("c.proveedor_id = ?"); params.append(int(proveedor_id))
    if producto_id:
        condiciones.append("l.producto_id = ?"); params.append(int(producto_id))
    if desde:
        condiciones.append("c.fecha >= ?"); params.append(leer_fecha(desde))
    if hasta:
        condiciones.append("c.fecha <= ?"); params.append(leer_fecha(hasta))
    if compra_id:
        condiciones.append("c.id = ?"); params.append(int(compra_id))
    sql = SQL_COMPRAS + (" WHERE " + " AND ".join(condiciones) if condiciones else "")
    return con.execute(sql + " ORDER BY c.fecha DESC, c.id DESC, l.id", params).fetchall()


def listar_facturas(con):
    """Una fila por compra (factura), para elegir de cuál imprimir etiquetas."""
    return con.execute("""
        SELECT c.id, c.fecha, c.nro_factura, c.origen, COALESCE(pr.nombre, '') AS proveedor,
               COUNT(l.id) AS renglones, SUM(l.cantidad) AS unidades
        FROM compras c
        JOIN lotes l ON l.compra_id = c.id
        LEFT JOIN proveedores pr ON pr.id = c.proveedor_id
        GROUP BY c.id
        ORDER BY c.fecha DESC, c.id DESC""").fetchall()


def obtener_lote(con, lote_id: int):
    return con.execute(SQL_COMPRAS + " WHERE l.id = ?", (lote_id,)).fetchone()


def listar_ventas(con):
    sql = """
    SELECT v.id, v.fecha, COALESCE(cl.nombre, v.cliente) AS cliente, v.medio_pago,
           vi.cantidad, vi.precio_unit, vi.costo_fifo,
           vi.cantidad * vi.precio_unit AS total,
           vi.cantidad * vi.precio_unit - vi.costo_fifo AS ganancia,
           p.codigo, p.sku, p.nombre_interno
    FROM ventas v
    JOIN venta_items vi ON vi.venta_id = v.id
    JOIN productos p ON p.id = vi.producto_id
    LEFT JOIN clientes cl ON cl.id = v.cliente_id
    ORDER BY v.fecha DESC, v.id DESC, vi.id
    """
    return con.execute(sql).fetchall()


def ultimas_ventas(con, limite: int = 20) -> list[dict]:
    """Ventas agrupadas (una entrada por venta con sus productos), la más nueva primero."""
    ventas: dict[int, dict] = {}
    for fila in listar_ventas(con):
        venta = ventas.get(fila["id"])
        if venta is None:
            if len(ventas) == limite:
                break
            venta = ventas[fila["id"]] = {
                "id": fila["id"], "fecha": fila["fecha"], "cliente": fila["cliente"],
                "medio_pago": fila["medio_pago"], "items": [], "total": 0.0, "ganancia": 0.0,
            }
        venta["items"].append(fila)
        venta["total"] += fila["total"]
        venta["ganancia"] += fila["ganancia"]
    return list(ventas.values())

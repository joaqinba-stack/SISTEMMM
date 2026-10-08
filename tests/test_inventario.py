import pytest

from app import inventario as inv


def test_leer_numero():
    assert inv.leer_numero("225.000") == 225000
    assert inv.leer_numero("₲ 1.250.000") == 1250000
    assert inv.leer_numero("2,5") == 2.5
    assert inv.leer_numero("2.5") == 2.5
    assert inv.leer_numero("") is None
    assert inv.guaranies(1250000) == "₲ 1.250.000"


def test_crear_producto_calcula_unitario_y_40_por_ciento(con):
    p = inv.crear_producto(con, {"nombre_interno": "Balanza", "proveedor": "Pacific",
                                 "cantidad": "10", "costo_total": "225.000",
                                 "nro_factura": "001-001-1", "fecha": "2026-06-05"})
    fila = inv.obtener_producto(con, p["id"])
    assert fila["stock"] == 10
    assert fila["costo_ultimo"] == 22500
    assert fila["precio_venta"] == 31500  # 22.500 + 40 %
    compra = inv.listar_compras(con)[0]
    assert compra["nro_factura"] == "001-001-1" and compra["proveedor"] == "Pacific"


def test_precio_editable(con):
    p = inv.crear_producto(con, {"nombre_interno": "X", "cantidad": 2, "costo_total": 1000,
                                 "precio_venta": "999"})
    assert inv.obtener_producto(con, p["id"])["precio_venta"] == 999


def test_fifo_consume_lote_mas_viejo_primero(con):
    p = inv.crear_producto(con, {"nombre_interno": "Molde", "cantidad": 5, "costo_total": 5000,
                                 "fecha": "2026-01-01", "proveedor": "A"})
    inv.registrar_compra(con, "2026-02-01", "F2", "A",
                         [{"producto_id": p["id"], "cantidad": 5, "costo_total": 10000}])
    r = inv.registrar_venta(con, p["id"], 7, 5000)
    # 5 unidades a 1.000 + 2 unidades a 2.000
    assert r["costo"] == 5 * 1000 + 2 * 2000
    assert r["ganancia"] == 7 * 5000 - 9000
    assert r["stock_restante"] == 3
    r2 = inv.registrar_venta(con, p["id"], 3, 5000)
    assert r2["costo"] == 3 * 2000
    assert inv.stock(con, p["id"]) == 0


def test_no_vende_sin_stock(con):
    p = inv.crear_producto(con, {"nombre_interno": "Molde", "cantidad": 1, "costo_total": 100})
    with pytest.raises(inv.ErrorNegocio, match="No alcanza"):
        inv.registrar_venta(con, p["id"], 2, 500)
    assert inv.stock(con, p["id"]) == 1


def test_aplicar_porcentaje_por_categoria(con):
    vela = inv.categoria_id(con, "Vela")
    a = inv.crear_producto(con, {"nombre_interno": "A", "categoria_id": vela,
                                 "cantidad": 1, "costo_total": 10000})
    b = inv.crear_producto(con, {"nombre_interno": "B", "categoria": "Yeso",
                                 "cantidad": 1, "costo_total": 10000})
    assert inv.aplicar_porcentaje(con, 100, vela) == 1
    assert inv.obtener_producto(con, a["id"])["precio_venta"] == 20000
    assert inv.obtener_producto(con, b["id"])["precio_venta"] == 14000


def test_editar_lote_bloqueado_si_hubo_ventas(con):
    p = inv.crear_producto(con, {"nombre_interno": "M", "cantidad": 4, "costo_total": 400})
    lote = inv.listar_compras(con)[0]
    inv.editar_lote(con, lote["lote_id"], {"cantidad": "6", "costo_total": "600",
                                           "fecha": "2026-03-01", "nro_factura": "F9",
                                           "proveedor": "Nuevo"})
    assert inv.stock(con, p["id"]) == 6
    inv.registrar_venta(con, p["id"], 1, 200)
    with pytest.raises(inv.ErrorNegocio, match="ya se vendieron"):
        inv.editar_lote(con, lote["lote_id"], {"cantidad": "8", "costo_total": "600"})
    with pytest.raises(inv.ErrorNegocio):
        inv.borrar_lote(con, lote["lote_id"])


def test_proveedor_normalizado(con):
    assert inv.proveedor_id(con, "Pacific ") == inv.proveedor_id(con, "pacific")


def test_buscar_coincidencia(con):
    prov = inv.proveedor_id(con, "Super K")
    p = inv.crear_producto(con, {"nombre_interno": "Molde vela rosa",
                                 "nombre_proveedor": "MOLDE VELA ROSA R01 M J27533",
                                 "proveedor": "Super K"})
    encontrado = inv.buscar_coincidencia(con, "MOLDE VELA ROSA R01 M J27533", prov)
    assert encontrado["id"] == p["id"]
    assert inv.buscar_coincidencia(con, "Pistola de calor", prov) is None

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
                                 "fecha": "2026-01-01", "proveedor": "A", "precio_venta": 5000})
    inv.registrar_compra(con, "2026-02-01", "F2", "A",
                         [{"producto_id": p["id"], "cantidad": 5, "costo_total": 10000}])
    r = inv.registrar_venta(con, [{"producto_id": p["id"], "cantidad": 7}])
    # 5 unidades a 1.000 + 2 unidades a 2.000
    assert r["costo"] == 5 * 1000 + 2 * 2000
    assert r["ganancia"] == 7 * 5000 - 9000
    assert inv.stock(con, p["id"]) == 3
    r2 = inv.registrar_venta(con, [{"producto_id": p["id"], "cantidad": 3}])
    assert r2["costo"] == 3 * 2000
    assert inv.stock(con, p["id"]) == 0


def test_venta_con_varios_productos_y_precio_de_la_ficha(con):
    a = inv.crear_producto(con, {"nombre_interno": "A", "cantidad": 4, "costo_total": 400,
                                 "precio_venta": 300})
    b = inv.crear_producto(con, {"nombre_interno": "B", "cantidad": 2, "costo_total": 1000,
                                 "precio_venta": 900})
    cliente = inv.crear_cliente(con, "Ana Pérez", "0981 111 222", "1234567-8")
    r = inv.registrar_venta(con, [{"producto_id": a["id"], "cantidad": 1, "precio_unit": 1},
                                  {"producto_id": b["id"], "cantidad": 2},
                                  {"producto_id": a["id"], "cantidad": 2}],
                            cliente["id"], "Efectivo", "2026-06-01")
    assert r["productos"] == 2
    assert r["total"] == 3 * 300 + 2 * 900  # el precio enviado se ignora
    assert inv.stock(con, a["id"]) == 1 and inv.stock(con, b["id"]) == 0
    venta = inv.ultimas_ventas(con)[0]
    assert venta["cliente"] == "Ana Pérez" and len(venta["items"]) == 2


def test_venta_todo_o_nada(con):
    a = inv.crear_producto(con, {"nombre_interno": "A", "cantidad": 5, "costo_total": 500})
    b = inv.crear_producto(con, {"nombre_interno": "B", "cantidad": 1, "costo_total": 100})
    with pytest.raises(inv.ErrorNegocio, match="No alcanza el stock de «B»"):
        inv.registrar_venta(con, [{"producto_id": a["id"], "cantidad": 2},
                                  {"producto_id": b["id"], "cantidad": 2}])
    assert inv.stock(con, a["id"]) == 5 and inv.stock(con, b["id"]) == 1
    assert con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 0
    with pytest.raises(inv.ErrorNegocio, match="al menos un producto"):
        inv.registrar_venta(con, [])


def test_clientes_sin_duplicados_y_busqueda(con):
    c1 = inv.crear_cliente(con, "  Benicia  Albrecht ", "0981 555", "4.567.890")
    c2 = inv.crear_cliente(con, "benicia albrecht", "", "4567890")
    assert c1["id"] == c2["id"] and c1["nombre"] == "Benicia Albrecht"
    otro = inv.crear_cliente(con, "Benicia Albrecht", "", "999")
    assert otro["id"] != c1["id"]
    assert [c["id"] for c in inv.buscar_clientes(con, "0981 555")] == [c1["id"]]
    assert len(inv.buscar_clientes(con, "benicia")) == 2
    with pytest.raises(inv.ErrorNegocio):
        inv.crear_cliente(con, "  ")


def test_fijar_margen_por_producto(con):
    a = inv.crear_producto(con, {"nombre_interno": "A", "cantidad": 1, "costo_total": 10000})
    b = inv.crear_producto(con, {"nombre_interno": "B", "cantidad": 1, "costo_total": 10000})
    assert inv.fijar_margen(con, a["id"], "100") == 20000
    assert inv.obtener_producto(con, a["id"])["margen_pct"] == 100
    assert inv.obtener_producto(con, b["id"])["precio_venta"] == 14000  # el otro no cambia
    sin_costo = inv.crear_producto(con, {"nombre_interno": "C"})
    with pytest.raises(inv.ErrorNegocio, match="no tiene costo"):
        inv.fijar_margen(con, sin_costo["id"], 50)
    with pytest.raises(inv.ErrorNegocio):
        inv.fijar_margen(con, a["id"], "-5")


def test_notificaciones_se_vacian(con):
    p = inv.crear_producto(con, {"nombre_interno": "Nuevo", "cantidad": 1, "costo_total": 100,
                                 "revisar": True})
    avisos = inv.notificaciones(con)
    assert [x["id"] for x in avisos["revisar"]] == [p["id"]]
    assert [x["id"] for x in avisos["poco_stock"]] == [p["id"]]
    inv.registrar_compra(con, None, "F", "", [{"producto_id": p["id"], "cantidad": 10,
                                               "costo_total": 1000}])
    con.execute("UPDATE productos SET revisar = 0 WHERE id = ?", (p["id"],))
    assert inv.notificaciones(con) == {"poco_stock": [], "revisar": []}


def test_filtros_de_compras(con):
    a = inv.crear_producto(con, {"nombre_interno": "A", "proveedor": "Pacific", "cantidad": 1,
                                 "costo_total": 100, "fecha": "2026-05-10"})
    inv.crear_producto(con, {"nombre_interno": "B", "proveedor": "Super K", "cantidad": 1,
                             "costo_total": 200, "fecha": "2026-06-10"})
    pacific = inv.proveedor_id(con, "Pacific")
    assert [c["nombre_interno"] for c in inv.listar_compras(con, proveedor_id=pacific)] == ["A"]
    assert [c["nombre_interno"] for c in inv.listar_compras(con, producto_id=a["id"])] == ["A"]
    assert [c["nombre_interno"] for c in inv.listar_compras(con, desde="2026-06-01")] == ["B"]
    assert [c["nombre_interno"] for c in inv.listar_compras(con, hasta="31/05/2026")] == ["A"]


def test_editar_lote_bloqueado_si_hubo_ventas(con):
    p = inv.crear_producto(con, {"nombre_interno": "M", "cantidad": 4, "costo_total": 400})
    lote = inv.listar_compras(con)[0]
    inv.editar_lote(con, lote["lote_id"], {"cantidad": "6", "costo_total": "600",
                                           "fecha": "2026-03-01", "nro_factura": "F9",
                                           "proveedor": "Nuevo"})
    assert inv.stock(con, p["id"]) == 6
    inv.registrar_venta(con, [{"producto_id": p["id"], "cantidad": 1}])
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


def test_unir_producto_creado_por_error(con):
    bueno = inv.crear_producto(con, {"nombre_interno": "Pistola de calor", "proveedor": "Pacific",
                                     "cantidad": 2, "costo_total": 80000})
    malo = inv.crear_producto(con, {"nombre_interno": "PISTOLA CALOR 300W XQ", "proveedor": "Pacific",
                                    "cantidad": 3, "costo_total": 120000, "revisar": True})
    inv.registrar_venta(con, [{"producto_id": malo["id"], "cantidad": 1}])
    r = inv.unir_productos(con, malo["id"], bueno["id"])
    assert r["stock"] == 4 and r["borrado"] == malo["codigo"]
    assert inv.obtener_producto(con, malo["id"]) is None
    assert {c["nombre_interno"] for c in inv.listar_compras(con)} == {"Pistola de calor"}
    assert inv.ultimas_ventas(con)[0]["items"][0]["nombre_interno"] == "Pistola de calor"
    # aprendió el nombre: la próxima factura se reconoce sola
    assert inv.buscar_coincidencia(con, "pistola calor 300w xq", None)["id"] == bueno["id"]
    with pytest.raises(inv.ErrorNegocio, match="distinto"):
        inv.unir_productos(con, bueno["id"], bueno["id"])


def test_precio_a_mano_calcula_el_porcentaje(con):
    p = inv.crear_producto(con, {"nombre_interno": "A", "cantidad": 2, "costo_total": 20000})
    assert inv.fijar_precio(con, p["id"], "15.000") == 50
    fila = inv.obtener_producto(con, p["id"])
    assert fila["precio_venta"] == 15000 and fila["margen_pct"] == 50
    sin_costo = inv.crear_producto(con, {"nombre_interno": "B", "margen_pct": 30})
    assert inv.fijar_precio(con, sin_costo["id"], "9000") == 30  # sin costo se guarda el precio y queda el %
    assert inv.obtener_producto(con, sin_costo["id"])["precio_venta"] == 9000
    with pytest.raises(inv.ErrorNegocio):
        inv.fijar_precio(con, p["id"], "")


def test_parecido_sin_importar_proveedor_ni_orden(con):
    assert inv.parecido("Balanza digital 10kg", "BALANZA 10KG DIGITAL") >= 0.86
    assert inv.parecido("Molde vela rosa", "Molde vela corazón") < 0.86
    assert inv.parecido("Balanza digital 10kg", "Balanza digital 2kg") < 0.86
    p = inv.crear_producto(con, {"nombre_interno": "Pistola de calor 300W", "proveedor": "Pacific"})
    otro_prov = inv.proveedor_id(con, "Super K")
    assert inv.buscar_coincidencia(con, "PISTOLA DE CALOR 300W", otro_prov)["id"] == p["id"]

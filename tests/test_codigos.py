from app import db, inventario as inv


def test_codigo_y_barra_automaticos(con):
    vela = inv.categoria_id(con, "Vela")
    a = inv.crear_producto(con, {"nombre_interno": "Molde Vela Rosa", "categoria_id": vela})
    b = inv.crear_producto(con, {"nombre_interno": "Molde Vela Rosa", "categoria": "Jabón"})
    assert (a["codigo"], b["codigo"]) == ("P-00001", "P-00002")
    # el código de barra es el mismo código del producto, sin importar la categoría
    assert (a["codigo_barra"], b["codigo_barra"]) == ("P-00001", "P-00002")
    assert "sku" not in a


def test_bases_viejas_pasan_la_barra_al_codigo():
    with db.conexion() as con:
        p = inv.crear_producto(con, {"nombre_interno": "Viejo"})
        con.execute("UPDATE productos SET codigo_barra = '20022001' WHERE id = ?", (p["id"],))
    db.inicializar()
    with db.conexion() as con:
        assert con.execute("SELECT codigo_barra FROM productos").fetchone()[0] == "P-00001"


def test_bases_viejas_pasan_el_sku_al_codigo():
    with db.conexion() as con:
        p = inv.crear_producto(con, {"nombre_interno": "Viejo"})
        con.execute("UPDATE productos SET sku = 'VIE-00001' WHERE id = ?", (p["id"],))
    db.inicializar()
    with db.conexion() as con:
        assert con.execute("SELECT sku FROM productos").fetchone()[0] == "P-00001"

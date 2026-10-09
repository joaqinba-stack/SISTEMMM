from app import db, inventario as inv


def test_codigo_y_barra_automaticos(con):
    vela = inv.categoria_id(con, "Vela")
    a = inv.crear_producto(con, {"nombre_interno": "Molde Vela Rosa", "categoria_id": vela})
    b = inv.crear_producto(con, {"nombre_interno": "Molde Vela Rosa", "categoria_id": vela})
    assert (a["codigo"], b["codigo"]) == ("P-00001", "P-00002")
    assert (a["codigo_barra"], b["codigo_barra"]) == ("20022001", "20022002")
    assert "sku" not in a  # ya no hay SKU: solo el código
    jabon = inv.crear_producto(con, {"nombre_interno": "Molde jabón", "categoria": "Jabón"})
    assert jabon["codigo_barra"] == "30033001"


def test_barra_continua_numeracion_existente(con):
    inv.crear_producto(con, {"nombre_interno": "Viejo", "categoria": "Yeso", "codigo_barra": "40044005"})
    nuevo = inv.crear_producto(con, {"nombre_interno": "Nuevo", "categoria": "Yeso"})
    assert nuevo["codigo_barra"] == "40044006"


def test_bases_viejas_pasan_el_sku_al_codigo():
    with db.conexion() as con:
        p = inv.crear_producto(con, {"nombre_interno": "Viejo"})
        con.execute("UPDATE productos SET sku = 'VIE-00001' WHERE id = ?", (p["id"],))
    db.inicializar()
    with db.conexion() as con:
        assert con.execute("SELECT sku FROM productos").fetchone()[0] == "P-00001"

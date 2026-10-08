from app import codigos, inventario as inv


def test_base_sku_como_el_excel():
    assert codigos.base_sku("Balanza Digital 10kg") == "BAL-DIG-10KG"
    assert codigos.base_sku("Vaso Inox 750 ml") == "VAS-INO-750-ML"
    assert codigos.base_sku("Pegamento para Pabilo redondo X20") == "PEG-PAB-RED-X20"
    assert codigos.base_sku("Jabón de Ñandutí") == "JAB-NAN"
    assert codigos.base_sku("  ") == "PRO"


def test_sku_codigo_y_barra_automaticos(con):
    vela = inv.categoria_id(con, "Vela")
    a = inv.crear_producto(con, {"nombre_interno": "Molde Vela Rosa", "categoria_id": vela})
    b = inv.crear_producto(con, {"nombre_interno": "Molde Vela Rosa", "categoria_id": vela})
    assert a["sku"] == "MOL-VEL-ROS-00001"
    assert b["sku"] == "MOL-VEL-ROS-00002"
    assert (a["codigo"], b["codigo"]) == ("P-00001", "P-00002")
    assert (a["codigo_barra"], b["codigo_barra"]) == ("20022001", "20022002")
    jabon = inv.crear_producto(con, {"nombre_interno": "Molde jabón", "categoria": "Jabón"})
    assert jabon["codigo_barra"] == "30033001"


def test_barra_continua_numeracion_existente(con):
    inv.crear_producto(con, {"nombre_interno": "Viejo", "categoria": "Yeso",
                             "codigo_barra": "40044005", "sku": "VIE-00001"})
    nuevo = inv.crear_producto(con, {"nombre_interno": "Nuevo", "categoria": "Yeso"})
    assert nuevo["codigo_barra"] == "40044006"


def test_sku_repetido_del_excel_no_choca(con):
    inv.crear_producto(con, {"nombre_interno": "Cosa", "sku": "COS-00001"})
    otro = inv.crear_producto(con, {"nombre_interno": "Cosa", "sku": "COS-00001"})
    assert otro["sku"] == "COS-00002"

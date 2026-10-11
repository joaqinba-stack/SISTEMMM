import io

import pytest
from pypdf import PdfReader

from app import config, etiquetas_pdf, impresora
from app.etiqueta_diseno import TAMANOS, diseno_etiqueta

PRODUCTO = {"id": 1, "nombre_interno": "Molde yeso maceta niña soñadora grande J27625",
            "codigo_barra": "P-00015", "precio_venta": 75000, "codigo": "P-00015",
            "sku": "MOL-YES-MAC-NIÑ-00001"}


@pytest.mark.parametrize("clave", TAMANOS)
@pytest.mark.parametrize("con_precio", [True, False])
def test_diseno_entra_en_cada_tamano(clave, con_precio):
    ancho, alto = TAMANOS[clave]
    d = diseno_etiqueta(ancho, alto, "P-00015", con_precio)
    for caja in d.cajas():
        assert caja.x >= 0 and caja.y >= 0 and caja.ancho > 0 and caja.alto > 0
        assert caja.x + caja.ancho <= ancho + 1e-6 and caja.abajo <= alto + 1e-6
    assert d.barras.ancho <= ancho - 2 * d.margen
    assert d.barras.alto >= 8  # barras altas para que el lector las lea bien
    assert d.nombre.abajo <= d.barras.y and d.barras.abajo <= d.numero.y + 1e-6
    if d.precio and not d.precio_a_la_derecha:
        assert d.numero.abajo <= d.precio.y


@pytest.mark.parametrize("clave", TAMANOS)
def test_pdf_de_rollo_tiene_el_tamano_de_la_etiqueta(clave):
    contenido = etiquetas_pdf.rollo_pdf([PRODUCTO] * 3, clave, "prueba")
    lector = PdfReader(io.BytesIO(contenido))
    assert len(lector.pages) == 3
    ancho, alto = TAMANOS[clave]
    caja = lector.pages[0].mediabox
    assert abs(float(caja.width) - ancho * 72 / 25.4) < 0.5 and abs(float(caja.height) - alto * 72 / 25.4) < 0.5
    assert "P-00015" in lector.pages[0].extract_text()


def test_vista_previa_svg():
    svg = etiquetas_pdf.vista_svg(PRODUCTO, "40x40")
    assert 'viewBox="0 0 40 40"' in svg and "Gs. 75.000" in svg and "<rect" in svg


def test_tspl_para_impresora_termica():
    otro = {**PRODUCTO, "id": 2, "nombre_interno": "Vela", "codigo_barra": "P-00002", "codigo": "P-00002"}
    datos = impresora.tspl_etiquetas(impresora.agrupar([PRODUCTO, PRODUCTO, PRODUCTO, otro]), "55x28").decode()
    assert datos.startswith("SIZE 55 mm,28 mm\r\n")
    assert "PRINT 1,3" in datos and "PRINT 1,1" in datos
    assert '"128"' in datos and 'BARCODE' in datos and '"P-00015"' in datos and "Gs. 75.000" in datos
    assert "NINA" in datos.upper() and "ñ" not in datos  # sin tildes ni ñ
    assert "SIZE 40 mm,40 mm" in impresora.tspl_etiquetas([(PRODUCTO, 1)], "40x40").decode()


def test_enviar_sin_impresora_configurada(monkeypatch):
    monkeypatch.setattr(config, "IMPRESORA_NOMBRE", "")
    monkeypatch.setattr(config, "IMPRESORA_IP", "")
    assert not impresora.configurada()
    with pytest.raises(impresora.ErrorImpresora, match="IMPRESORA_NOMBRE"):
        impresora.enviar(b"x")

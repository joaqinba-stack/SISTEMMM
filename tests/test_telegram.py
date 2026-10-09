import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from app import config, db, factura_ia, impresora, inventario as inv, telegram_bot as bot

LEIDO = {
    "es_factura": True,
    "proveedor": "Pacific",
    "nro_factura": "001-003-0036933",
    "fecha": "2026-06-05",
    "items": [
        {"descripcion": "Pistola de Calor 50HZ 300W", "cantidad": 6, "precio_unitario": 40500,
         "total": 243000},
        {"descripcion": "Cortador de Pabilo", "cantidad": 6, "precio_unitario": 27000,
         "total": 162000},
    ],
}


def _foto_jpg() -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(salida, format="JPEG")
    return salida.getvalue()


def test_flujo_foto_confirmar_guardar(monkeypatch):
    with db.conexion() as con:
        existente = inv.crear_producto(con, {
            "nombre_interno": "Pistola de calor", "nombre_proveedor": "Pistola de Calor 50HZ 300W",
            "proveedor": "Pacific", "cantidad": 2, "costo_total": 80000})
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", {"111"})
    monkeypatch.setattr(factura_ia, "extraer", AsyncMock(return_value=LEIDO))

    aviso = MagicMock(edit_text=AsyncMock())
    archivo = MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(_foto_jpg())))
    mensaje = MagicMock(photo=[MagicMock(get_file=AsyncMock(return_value=archivo))],
                        reply_text=AsyncMock(return_value=aviso))
    update = SimpleNamespace(effective_user=SimpleNamespace(id=111), effective_message=mensaje)

    asyncio.run(bot.recibir_factura(update, None))
    texto = aviso.edit_text.call_args.args[0]
    assert "001-003-0036933" in texto and "✔️" in texto and "🆕" in texto
    teclado = aviso.edit_text.call_args.kwargs["reply_markup"]
    datos_boton = teclado.inline_keyboard[0][0].callback_data
    assert datos_boton.startswith("g:")

    consulta = MagicMock(data=datos_boton, answer=AsyncMock(), edit_message_text=AsyncMock())
    update2 = SimpleNamespace(effective_user=SimpleNamespace(id=111), callback_query=consulta)
    asyncio.run(bot.responder_boton(update2, None))
    assert "Listo" in consulta.edit_message_text.call_args.args[0]

    with db.conexion() as con:
        assert inv.obtener_producto(con, existente["id"])["stock"] == 8
        nuevo = con.execute("SELECT * FROM productos WHERE nombre_interno = 'Cortador de Pabilo'").fetchone()
        assert nuevo["revisar"] == 1 and nuevo["sku"].startswith("COR-PAB")
        assert nuevo["precio_venta"] == 37800  # 27.000 + 40 %
        assert inv.stock(con, nuevo["id"]) == 6
        compra = con.execute("SELECT * FROM compras WHERE origen = 'telegram'").fetchone()
        assert compra["foto_factura"].startswith("facturas/")

    # Volver a tocar el botón no guarda dos veces
    asyncio.run(bot.responder_boton(update2, None))
    assert "ya fue guardada" in consulta.edit_message_text.call_args.args[0]


def test_factura_repetida_avisa():
    with db.conexion() as con:
        p = inv.crear_producto(con, {"nombre_interno": "Algo", "proveedor": "Pacific",
                                     "cantidad": 1, "costo_total": 1, "nro_factura": "001-003-0036933"})
    compra = bot.preparar_compra(LEIDO, None)
    assert compra["repetida"]
    assert "ya fue cargada" in bot.texto_resumen(compra)


def test_usuario_no_permitido(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", {"111"})
    extraer = AsyncMock()
    monkeypatch.setattr(factura_ia, "extraer", extraer)
    mensaje = MagicMock(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=999), effective_message=mensaje)
    asyncio.run(bot.recibir_factura(update, None))
    assert "999" in mensaje.reply_text.call_args.args[0]
    extraer.assert_not_called()


def test_extraer_con_claude_simulado(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "clave-prueba")
    respuesta = SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="text", text=json.dumps({**LEIDO, "items": LEIDO["items"] + [
            {"descripcion": "", "cantidad": 0, "precio_unitario": 0, "total": 0}]}))])
    crear = AsyncMock(return_value=respuesta)
    cliente = MagicMock()
    cliente.beta.messages.create = crear
    monkeypatch.setattr(factura_ia.anthropic, "AsyncAnthropic", MagicMock(return_value=cliente))

    datos = asyncio.run(factura_ia.extraer(_foto_jpg(), "image/jpeg"))
    assert len(datos["items"]) == 2 and datos["proveedor"] == "Pacific"
    kwargs = crear.call_args.kwargs
    assert kwargs["model"] == config.CLAUDE_MODEL
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["messages"][0]["content"][0]["type"] == "image"


def test_extraer_rechazo(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "clave-prueba")
    cliente = MagicMock()
    cliente.beta.messages.create = AsyncMock(
        return_value=SimpleNamespace(stop_reason="refusal", content=[]))
    monkeypatch.setattr(factura_ia.anthropic, "AsyncAnthropic", MagicMock(return_value=cliente))
    try:
        asyncio.run(factura_ia.extraer(_foto_jpg()))
    except factura_ia.ErrorLectura as exc:
        assert "otra foto" in str(exc)
    else:
        raise AssertionError("debía fallar")


def test_corregir_renglon_en_telegram_y_aprender(monkeypatch):
    with db.conexion() as con:
        cortador = inv.crear_producto(con, {"nombre_interno": "Cortador de pabilo profesional",
                                            "proveedor": "Pacific", "cantidad": 1, "costo_total": 27000})
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", {"111"})
    monkeypatch.setattr(factura_ia, "extraer", AsyncMock(return_value=LEIDO))
    monkeypatch.setattr(config, "IMPRESORA_NOMBRE", "Xprinter")
    enviados = []
    monkeypatch.setattr(impresora, "enviar", lambda datos: enviados.append(datos))

    aviso = MagicMock(edit_text=AsyncMock())
    archivo = MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(_foto_jpg())))
    mensaje = MagicMock(photo=[MagicMock(get_file=AsyncMock(return_value=archivo))],
                        reply_text=AsyncMock(return_value=aviso))
    usuario = SimpleNamespace(id=111)
    asyncio.run(bot.recibir_factura(SimpleNamespace(effective_user=usuario, effective_message=mensaje), None))
    teclado = aviso.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    clave = teclado[0][0].callback_data.split(":")[1]
    corregir = [b.callback_data for fila in teclado[1:] for b in fila]
    assert corregir == [f"k:{clave}:0", f"k:{clave}:1"]

    contexto = SimpleNamespace(user_data={})
    def boton(datos):
        c = MagicMock(data=datos, answer=AsyncMock(), edit_message_text=AsyncMock(),
                      message=MagicMock(reply_html=AsyncMock(), reply_text=AsyncMock()))
        asyncio.run(bot.responder_boton(SimpleNamespace(effective_user=usuario, callback_query=c), contexto))
        return c

    boton(f"k:{clave}:1")  # corregir "Cortador de Pabilo"
    assert contexto.user_data["corrigiendo"] == (clave, 1)
    texto = MagicMock(text="cortador", reply_text=AsyncMock(), reply_html=AsyncMock())
    asyncio.run(bot.texto_libre(SimpleNamespace(effective_user=usuario, effective_message=texto), contexto))
    opciones = texto.reply_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert opciones[0][0].callback_data == f"p:{clave}:1:{cortador['id']}"

    c = boton(opciones[0][0].callback_data)
    assert "Cortador de pabilo profesional" in c.edit_message_text.call_args.args[0]
    c = boton(f"g:{clave}")
    final = c.edit_message_text.call_args
    assert "Listo" in final.args[0]
    imprimir = final.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    with db.conexion() as con:
        assert inv.stock(con, cortador["id"]) == 7  # 1 + 6 de la factura, sin crear otro producto
        assert con.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 2
        assert inv.buscar_coincidencia(con, "Cortador de Pabilo", None)["id"] == cortador["id"]

    c = boton(imprimir)
    assert "mandé 12 etiqueta" in c.message.reply_text.call_args.args[0]  # 6 pistolas + 6 cortadores
    assert enviados[0].count(b"PRINT 1,6") == 2

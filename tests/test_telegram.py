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
        assert nuevo["revisar"] == 1 and nuevo["codigo"].startswith("P-")
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


def test_sin_lista_de_permitidos_es_de_uso_libre(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", set())
    mensaje = MagicMock(reply_html=AsyncMock(), reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=999), effective_message=mensaje)
    asyncio.run(bot.cmd_inicio(update, None))
    mensaje.reply_html.assert_called_once()
    mensaje.reply_text.assert_not_called()


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
    botones = [b.callback_data for fila in teclado[1:] for b in fila]
    # el cortador se parece a uno que ya existe: se ofrece con un botón, además de Corregir
    assert botones == [f"p:{clave}:1:{cortador['id']}", f"k:{clave}:0", f"k:{clave}:1"]
    assert "Se parece a" in aviso.edit_text.call_args.args[0]

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


def test_mismo_producto_de_otro_proveedor_no_crea_codigo_nuevo(monkeypatch):
    with db.conexion() as con:
        cortador = inv.crear_producto(con, {"nombre_interno": "Cortador de Pabilo", "proveedor": "Pacific",
                                            "cantidad": 2, "costo_total": 54000, "fecha": "2026-09-01"})
        balanza = inv.crear_producto(con, {"nombre_interno": "Balanza digital 10kg", "proveedor": "Pacific",
                                           "cantidad": 1, "costo_total": 22500, "fecha": "2026-09-01"})
    leido = {"es_factura": True, "proveedor": "Super K", "nro_factura": "001-001-9", "fecha": "2026-10-01",
             "items": [
                 # la IA lo reconoció por la lista de productos aunque está escrito distinto
                 {"descripcion": "CORT. PABILO INOX X1", "cantidad": 3, "precio_unitario": 25000,
                  "total": 75000, "codigo_existente": cortador["codigo"]},
                 # sin ayuda de la IA, igual se reconoce por el nombre (otro orden de palabras)
                 {"descripcion": "BALANZA 10KG DIGITAL", "cantidad": 1, "precio_unitario": 23000,
                  "total": 23000, "codigo_existente": ""},
             ]}
    compra = bot.preparar_compra(leido, None)
    assert [i["producto_id"] for i in compra["items"]] == [cortador["id"], balanza["id"]]
    assert compra["items"][0]["aprender"] is True
    bot.guardar_compra(compra)
    with db.conexion() as con:
        assert con.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 2  # no se creó ninguno nuevo
        assert inv.stock(con, cortador["id"]) == 5
        assert inv.obtener_producto(con, cortador["id"])["proveedor"] == "Super K"  # última compra
        assert inv.buscar_coincidencia(con, "cort. pabilo inox x1")["id"] == cortador["id"]  # aprendido


def test_la_ia_recibe_la_lista_de_productos(monkeypatch):
    with db.conexion() as con:
        inv.crear_producto(con, {"nombre_interno": "Cortador de Pabilo"})
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", {"111"})
    extraer = AsyncMock(return_value={**LEIDO, "items": []})
    monkeypatch.setattr(factura_ia, "extraer", extraer)
    archivo = MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(_foto_jpg())))
    mensaje = MagicMock(photo=[MagicMock(get_file=AsyncMock(return_value=archivo))],
                        reply_text=AsyncMock(return_value=MagicMock(edit_text=AsyncMock())))
    asyncio.run(bot.recibir_factura(SimpleNamespace(effective_user=SimpleNamespace(id=111),
                                                    effective_message=mensaje), None))
    assert ("P-00001", "Cortador de Pabilo") in extraer.call_args.args[2]


def _cdc(nro="0010020000123", fecha="20261001") -> str:
    base = "01" + "80012345" + "0" + nro + "2" + fecha + "1" + "123456789"
    return base + str(factura_ia._dv_modulo11(base))


def test_leer_cdc():
    datos = factura_ia.leer_cdc(" ".join([_cdc()[i:i + 4] for i in range(0, 44, 4)]))
    assert datos["nro_factura"] == "001-002-0000123" and datos["fecha"] == "2026-10-01"
    assert datos["ruc_emisor"] == "80012345-0"
    mal_leido = _cdc()[:20] + str((int(_cdc()[20]) + 1) % 10) + _cdc()[21:]
    assert factura_ia.leer_cdc(mal_leido) is None
    assert factura_ia.leer_cdc("") is None and factura_ia.leer_cdc("123") is None


def test_el_cdc_corrige_numero_y_fecha():
    leido = {**LEIDO, "nro_factura": "001-002-0000128", "fecha": "2026-10-07", "cdc": _cdc()}
    compra = bot.preparar_compra(leido, None)
    assert compra["nro_factura"] == "001-002-0000123" and compra["fecha"] == "2026-10-01"
    texto = bot.texto_resumen(compra)
    assert "confirmados con el CDC" in texto and "001-002-0000128" in texto

    sin_cdc = bot.preparar_compra({**LEIDO, "cdc": "1234"}, None)  # CDC mal leído: no se usa
    assert sin_cdc["nro_factura"] == LEIDO["nro_factura"] and not sin_cdc["avisos"]


def test_avisa_si_los_renglones_no_suman_el_total():
    assert not bot.preparar_compra({**LEIDO, "total_factura": 405000}, None)["avisos"]
    compra = bot.preparar_compra({**LEIDO, "total_factura": 450000}, None)
    assert "405.000" in compra["avisos"][0] and "450.000" in compra["avisos"][0]
    assert "450.000" in bot.texto_resumen(compra)


def test_resumen_muestra_el_texto_de_la_factura_junto_al_producto():
    with db.conexion() as con:
        pistola = inv.crear_producto(con, {"nombre_interno": "Pistola de calor chica"})
    leido = {**LEIDO, "items": [{**LEIDO["items"][0], "codigo_existente": pistola["codigo"]}]}
    texto = bot.texto_resumen(bot.preparar_compra(leido, None))
    assert "Pistola de calor chica" in texto
    assert "En la factura: «Pistola de Calor 50HZ 300W»" in texto


def test_la_ia_recibe_tambien_los_nombres_aprendidos():
    with db.conexion() as con:
        p = inv.crear_producto(con, {"nombre_interno": "Cortador de Pabilo"})
        inv.agregar_alias(con, p["id"], "CORT. PABILO INOX X1")
        catalogo = dict(inv.catalogo_para_ia(con))
    assert catalogo[p["codigo"]] == "Cortador de Pabilo (también: CORT. PABILO INOX X1)"


# ---------------------------------------------------------------- facturas largas

def _leido_largo(cantidad=60):
    return {**LEIDO, "items": [
        {"descripcion": f"Aceite esencial aroma número {n} para velas y jabones 10ml", "cantidad": 3,
         "precio_unitario": 12000, "total": 36000} for n in range(1, cantidad + 1)]}


def _imagen(ancho, alto) -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), "white").save(salida, format="JPEG")
    return salida.getvalue()


def _mensaje_con(archivo_bytes, como_foto=True):
    aviso = MagicMock(edit_text=AsyncMock(return_value=MagicMock(delete=AsyncMock())))
    archivo = MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(archivo_bytes)))
    enviados = []

    async def responder(texto, **kwargs):
        if not enviados and "Recibí la foto" in texto:
            enviados.append(("aviso", texto, kwargs))
            return aviso
        enviados.append(("parte", texto, kwargs))
        return MagicMock(delete=AsyncMock(), message_id=len(enviados))
    if como_foto:
        mensaje = MagicMock(photo=[MagicMock(get_file=AsyncMock(return_value=archivo))])
    else:
        mensaje = MagicMock(photo=[], document=MagicMock(mime_type="image/jpeg",
                                                         get_file=AsyncMock(return_value=archivo)))
    mensaje.reply_text = responder
    return mensaje, aviso, enviados


def test_partir_respeta_el_largo_y_no_corta_lineas():
    lineas = [f"{n}. <b>Producto {n}</b>\n     3 × ₲ 12.000" for n in range(200)]
    partes = bot.partir(lineas, 500)
    assert len(partes) > 1 and all(len(p) <= 500 for p in partes)
    assert "\n".join(partes) == "\n".join(lineas)  # no se pierde ni se corta nada


def test_resumen_largo_va_en_partes_con_los_botones_al_final(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", set())
    monkeypatch.setattr(factura_ia, "extraer", AsyncMock(return_value=_leido_largo()))
    mensaje, aviso, enviados = _mensaje_con(_foto_jpg())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_message=mensaje)
    asyncio.run(bot.recibir_factura(update, None))

    primera = aviso.edit_text.call_args
    partes = [primera.args[0]] + [t for tipo, t, _ in enviados if tipo == "parte"]
    assert len(partes) >= 2 and all(len(p) <= 4096 for p in partes)
    assert partes[0].startswith("📄 <i>Parte 1 de") and "Productos: <b>60</b>" in partes[0]
    assert "60. 🆕" in partes[-1] and "Total: ₲ 2.160.000" in partes[-1]
    assert primera.kwargs.get("reply_markup") is None  # los botones van en la última parte
    teclado = enviados[-1][2]["reply_markup"].inline_keyboard
    datos = [b.callback_data for fila in teclado for b in fila]
    clave = datos[0].split(":")[1]
    # con 60 renglones: un solo botón para corregir, no 60
    assert f"n:{clave}" in datos and not any(d.startswith("k:") for d in datos)
    assert sum(len(fila) for fila in teclado) <= bot.MAX_BOTONES

    # corregir por número: se escribe 37 y se pide el producto correcto de ese renglón
    contexto = SimpleNamespace(user_data={})
    consulta = MagicMock(data=f"n:{clave}", answer=AsyncMock(), edit_message_text=AsyncMock(),
                         message=MagicMock(reply_html=AsyncMock(), reply_text=AsyncMock()))
    asyncio.run(bot.responder_boton(SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                                    callback_query=consulta), contexto))
    assert "del 1 al 60" in consulta.message.reply_html.call_args.args[0]
    texto = MagicMock(text="37", reply_text=AsyncMock(), reply_html=AsyncMock())
    asyncio.run(bot.texto_libre(SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                                effective_message=texto), contexto))
    assert contexto.user_data["corrigiendo"] == (clave, 36)
    assert "Producto 37" in texto.reply_html.call_args.args[0]

    # marcar el 37 como nuevo vuelve a mandar el resumen entero y borra las partes viejas
    viejas = list(bot.PENDIENTES[clave]["mensajes"])
    nuevo = MagicMock(data=f"p:{clave}:36:0", answer=AsyncMock(), edit_message_text=AsyncMock(),
                      message=MagicMock(message_id=999, reply_text=AsyncMock(
                          return_value=MagicMock(delete=AsyncMock()))))
    asyncio.run(bot.responder_boton(SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                                    callback_query=nuevo), contexto))
    assert "actualicé el resumen" in nuevo.edit_message_text.call_args.args[0]
    assert all(m.delete.await_count == 1 for m in viejas)
    assert nuevo.message.reply_text.await_count == len(partes)

    # guardar: el mensaje final también puede ser largo y va en partes
    guardar = MagicMock(data=f"g:{clave}", answer=AsyncMock(), edit_message_text=AsyncMock(),
                        message=MagicMock(reply_text=AsyncMock()))
    asyncio.run(bot.responder_boton(SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                                    callback_query=guardar), contexto))
    assert "Listo" in guardar.edit_message_text.call_args.args[0]
    todos = [guardar.edit_message_text.call_args.args[0]] + [
        c.args[0] for c in guardar.message.reply_text.call_args_list]
    assert all(len(t) <= 4096 for t in todos) and sum(t.count("ahora hay") for t in todos) == 60
    with db.conexion() as con:
        assert con.execute("SELECT COUNT(*) FROM lotes").fetchone()[0] == 60


def test_si_falla_al_mostrar_avisa_y_no_queda_colgado(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", set())
    monkeypatch.setattr(factura_ia, "extraer", AsyncMock(return_value=LEIDO))
    monkeypatch.setattr(bot, "teclado_resumen", MagicMock(side_effect=RuntimeError("Message_too_long")))
    mensaje, aviso, _ = _mensaje_con(_foto_jpg())
    asyncio.run(bot.recibir_factura(SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                                    effective_message=mensaje), None))
    assert "Hubo un problema" in aviso.edit_text.call_args.args[0]


def test_foto_larga_achicada_por_telegram_avisa_mandar_como_archivo(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USUARIOS_PERMITIDOS", set())
    monkeypatch.setattr(factura_ia, "extraer", AsyncMock(return_value=LEIDO))
    mensaje, aviso, _ = _mensaje_con(_imagen(299, 1280))  # como llegó la factura de 95 cm
    asyncio.run(bot.recibir_factura(SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                                    effective_message=mensaje), None))
    assert "Mandala como archivo" in aviso.edit_text.call_args.args[0]
    # la misma imagen mandada como archivo no avisa
    mensaje, aviso, _ = _mensaje_con(_imagen(299, 1280), como_foto=False)
    asyncio.run(bot.recibir_factura(SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                                    effective_message=mensaje), None))
    assert "Mandala como archivo" not in aviso.edit_text.call_args.args[0]


def test_avisa_si_faltan_productos():
    compra = bot.preparar_compra({**LEIDO, "total_factura": 900000}, None)
    assert "falten productos" in compra["avisos"][0] and "faltan ₲ 495.000" in compra["avisos"][0]
    assert "aviso(s) al final" in bot.texto_resumen(compra)


def test_factura_larga_se_corta_en_tramos_que_se_pisan():
    tramos = factura_ia._preparar_imagenes(_imagen(974, 4160), "image/jpeg")
    assert len(tramos) == 4
    medidas = [Image.open(io.BytesIO(t)).size for t, _ in tramos]
    assert all(max(m) <= factura_ia.LADO_IA and m[0] * m[1] <= factura_ia.PIXELES_IA for m in medidas)
    assert sum(alto for _, alto in medidas) > 4160  # se pisan: no se pierde ningún renglón
    # una tira muy ancha se lleva a 1.000 px de ancho; una foto normal va entera
    ancha = factura_ia._preparar_imagenes(_imagen(3000, 30000), "image/jpeg")
    assert Image.open(io.BytesIO(ancha[0][0])).width <= 1000
    assert len(factura_ia._preparar_imagenes(_imagen(1500, 2000), "image/jpeg")) == 1
    assert len(factura_ia._preparar_imagenes(_imagen(299, 1280), "image/jpeg")) == 1
    assert len(factura_ia._preparar_imagenes(_imagen(500, 60000), "image/jpeg")) <= factura_ia.MAX_TRAMOS


def test_extraer_factura_larga_manda_los_tramos_en_orden(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "clave-prueba")
    respuesta = SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="text", text=json.dumps(LEIDO))])
    crear = AsyncMock(return_value=respuesta)
    cliente = MagicMock()
    cliente.beta.messages.create = crear
    monkeypatch.setattr(factura_ia.anthropic, "AsyncAnthropic", MagicMock(return_value=cliente))
    asyncio.run(factura_ia.extraer(_imagen(974, 4160), "image/jpeg"))
    contenido = crear.call_args.kwargs["messages"][0]["content"]
    assert [b["type"] for b in contenido[:4]] == ["text", "image", "text", "image"]
    assert contenido[0]["text"] == "Parte 1 de 4:" and contenido[6]["text"] == "Parte 4 de 4:"
    assert "viene en 4 imágenes" in contenido[-1]["text"]

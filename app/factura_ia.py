"""Lectura de fotos de facturas con Claude (visión)."""
import base64
import io
import json
import logging

import anthropic
from PIL import Image, ImageOps

from app import config

log = logging.getLogger(__name__)


class ErrorLectura(Exception):
    """No se pudo leer la factura; el mensaje es apto para mostrar a la persona."""


ESQUEMA = {
    "type": "object",
    "properties": {
        "es_factura": {"type": "boolean"},
        "proveedor": {"type": "string"},
        "nro_factura": {"type": "string"},
        "fecha": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "descripcion": {"type": "string"},
                    "cantidad": {"type": "number"},
                    "precio_unitario": {"type": "number"},
                    "total": {"type": "number"},
                },
                "required": ["descripcion", "cantidad", "precio_unitario", "total"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["es_factura", "proveedor", "nro_factura", "fecha", "items"],
    "additionalProperties": False,
}

INSTRUCCIONES = """Esta es la foto de una factura de compra de un comercio de Paraguay.
Extraé los datos para cargarlos en un sistema de inventario.

- proveedor: nombre comercial o razón social del vendedor (quien emite la factura), no del cliente.
- nro_factura: número completo tal cual figura, normalmente con el formato 001-001-0000000.
- fecha: fecha de emisión en formato AAAA-MM-DD. Si no se lee, dejala vacía.
- items: un elemento por cada renglón de producto comprado.
  - descripcion: el texto del producto tal como aparece en la factura.
  - cantidad: unidades compradas.
  - precio_unitario: precio por unidad en guaraníes, IVA incluido.
  - total: importe del renglón en guaraníes (cantidad x precio unitario).
- Los montos están en guaraníes y no tienen decimales; el punto separa los miles
  (por ejemplo 22.500 son veintidós mil quinientos). Devolvé los números sin puntos.
- No incluyas renglones de subtotales, IVA, descuentos generales ni el total de la factura.
- Si la imagen no es una factura o un ticket de compra, poné es_factura en false y items vacío.
- Si algún dato no se puede leer, dejá el texto vacío o el número en 0. No inventes datos."""


def _preparar_imagen(contenido: bytes, media_type: str) -> tuple[bytes, str]:
    """Achica fotos grandes para que el envío sea rápido y barato. Los PDF van tal cual."""
    if media_type == "application/pdf":
        return contenido, media_type
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(contenido)))
        img = img.convert("RGB")
        img.thumbnail((2000, 2000))
        salida = io.BytesIO()
        img.save(salida, format="JPEG", quality=88)
        return salida.getvalue(), "image/jpeg"
    except Exception as exc:  # imagen dañada o formato raro
        raise ErrorLectura("No pude abrir la imagen. Probá mandar la foto otra vez.") from exc


def _bloque_archivo(contenido: bytes, media_type: str) -> dict:
    datos = base64.standard_b64encode(contenido).decode("utf-8")
    tipo = "document" if media_type == "application/pdf" else "image"
    return {"type": tipo, "source": {"type": "base64", "media_type": media_type, "data": datos}}


async def extraer(contenido: bytes, media_type: str = "image/jpeg") -> dict:
    """Devuelve {es_factura, proveedor, nro_factura, fecha, items:[{descripcion, cantidad,
    precio_unitario, total}]}."""
    if not config.ANTHROPIC_API_KEY:
        raise ErrorLectura("Falta configurar ANTHROPIC_API_KEY para poder leer facturas.")
    contenido, media_type = _preparar_imagen(contenido, media_type)
    cliente = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        respuesta = await cliente.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
            messages=[{
                "role": "user",
                "content": [_bloque_archivo(contenido, media_type),
                            {"type": "text", "text": INSTRUCCIONES}],
            }],
        )
    except anthropic.AuthenticationError as exc:
        raise ErrorLectura("La clave de Anthropic (ANTHROPIC_API_KEY) no es válida.") from exc
    except anthropic.RateLimitError as exc:
        raise ErrorLectura("El servicio está ocupado. Probá de nuevo en un minuto.") from exc
    except anthropic.APIStatusError as exc:
        log.exception("Error de la API de Claude")
        raise ErrorLectura("No pude leer la factura (error del servicio). Probá de nuevo.") from exc
    except anthropic.APIConnectionError as exc:
        raise ErrorLectura("No hay conexión a internet para leer la factura.") from exc

    if respuesta.stop_reason == "refusal":
        raise ErrorLectura("No pude leer esta imagen. Probá con otra foto de la factura.")
    if respuesta.stop_reason == "max_tokens":
        raise ErrorLectura("La factura es muy larga. Probá sacando fotos por partes.")
    texto = next((b.text for b in respuesta.content if b.type == "text"), "")
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ErrorLectura("No entendí la factura. Probá con una foto más clara.") from exc
    return limpiar(datos)


def limpiar(datos: dict) -> dict:
    items = []
    for item in datos.get("items") or []:
        cantidad = float(item.get("cantidad") or 0)
        unitario = float(item.get("precio_unitario") or 0)
        total = float(item.get("total") or 0)
        if cantidad <= 0 or not (item.get("descripcion") or "").strip():
            continue
        if total <= 0:
            total = cantidad * unitario
        items.append({
            "descripcion": item["descripcion"].strip(),
            "cantidad": cantidad,
            "precio_unitario": total / cantidad,
            "total": total,
        })
    return {
        "es_factura": bool(datos.get("es_factura", True)),
        "proveedor": (datos.get("proveedor") or "").strip(),
        "nro_factura": (datos.get("nro_factura") or "").strip(),
        "fecha": (datos.get("fecha") or "").strip(),
        "items": items,
    }

"""Lectura de fotos de facturas con Claude (visión)."""
import base64
import io
import json
import logging
import re

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
        "total_factura": {"type": "number"},
        "cdc": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "descripcion": {"type": "string"},
                    "cantidad": {"type": "number"},
                    "precio_unitario": {"type": "number"},
                    "total": {"type": "number"},
                    "codigo_existente": {"type": "string"},
                },
                "required": ["descripcion", "cantidad", "precio_unitario", "total", "codigo_existente"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["es_factura", "proveedor", "nro_factura", "fecha", "total_factura", "cdc", "items"],
    "additionalProperties": False,
}

INSTRUCCIONES = """Esta es la foto de una factura de compra de un comercio de Paraguay.
Extraé los datos para cargarlos en un sistema de inventario.

- proveedor: nombre comercial o razón social del vendedor (quien emite la factura), no del cliente.
- nro_factura: número completo tal cual figura, normalmente con el formato 001-001-0000000.
- fecha: fecha de emisión en formato AAAA-MM-DD. Si no se lee, dejala vacía.
- total_factura: el total general de la factura (el importe final a pagar), copiado tal cual figura,
  sin calcularlo. Si no se lee, 0.
- cdc: si es una factura electrónica, los 44 dígitos del CDC (código de control), sin espacios.
  Si no tiene CDC, dejalo vacío.
- items: un elemento por cada renglón de producto comprado.
  - descripcion: el texto del producto tal como aparece en la factura.
  - cantidad: unidades compradas.
  - precio_unitario: precio por unidad en guaraníes, IVA incluido.
  - total: importe del renglón en guaraníes (cantidad x precio unitario).
- Los montos están en guaraníes y no tienen decimales; el punto separa los miles
  (por ejemplo 22.500 son veintidós mil quinientos). Devolvé los números sin puntos.
- No incluyas renglones de subtotales, IVA, descuentos generales ni el total de la factura.
- Si la imagen no es una factura o un ticket de compra, poné es_factura en false y items vacío.
- Si algún dato no se puede leer, dejá el texto vacío o el número en 0. No inventes datos.
- codigo_existente: más abajo está la lista de productos que ya existen en el inventario.
  Si el renglón es el mismo producto que uno de la lista, poné su código (por ejemplo P-00028),
  aunque en la factura esté escrito distinto (abreviado, en mayúsculas, con otro orden o con el
  código del proveedor) y aunque venga de otro proveedor: el mismo producto se compra en varios
  lugares. Si es un producto distinto (otro tamaño, otro modelo, otra medida), dejalo vacío.
  Entre paréntesis, después de «también:», están otros nombres con los que ese producto ya figuró
  en facturas anteriores."""


def _dv_modulo11(numero: str) -> int:
    """Dígito verificador de la DNIT (módulo 11); es el mismo para el RUC y para el CDC."""
    total, k = 0, 2
    for digito in reversed(numero):
        total += int(digito) * k
        k = 2 if k == 11 else k + 1
    resto = total % 11
    return 11 - resto if resto > 1 else 0


def leer_cdc(texto: str) -> dict | None:
    """Datos que trae adentro el CDC de una factura electrónica (44 dígitos).
    Devuelve None si no es un CDC válido, por ejemplo si se leyó mal algún dígito."""
    cdc = re.sub(r"\D", "", texto or "")
    if len(cdc) != 44 or _dv_modulo11(cdc[:43]) != int(cdc[43]):
        return None
    fecha = cdc[25:33]
    return {
        "cdc": cdc,
        "ruc_emisor": f"{int(cdc[2:10])}-{cdc[10]}",
        "nro_factura": f"{cdc[11:14]}-{cdc[14:17]}-{cdc[17:24]}",
        "fecha": f"{fecha[:4]}-{fecha[4:6]}-{fecha[6:]}",
    }


# Facturas largas (tickets de varios centímetros): la IA achica cada imagen a ~1.568 px y ~1,15
# megapíxeles. Una tira de 95 cm entera quedaría ilegible, así que se corta en tramos que se pisan.
LARGA = 2.0              # alto / ancho desde el que una foto se considera una tira larga
LADO_IA = 1568           # lado más largo que la IA lee sin achicar
PIXELES_IA = 1_150_000   # píxeles por imagen que la IA lee sin achicar
ANCHO_TRAMO = 1000       # ancho al que se lleva la tira (8 cm de ticket se leen bien)
SOLAPE = 0.15            # cuánto se pisa un tramo con el siguiente, para no cortar renglones
MAX_TRAMOS = 20


def _abrir(contenido: bytes) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = 200_000_000  # tickets largos en alta resolución
    return ImageOps.exif_transpose(Image.open(io.BytesIO(contenido))).convert("RGB")


def es_tira_larga(contenido: bytes) -> bool:
    """Una foto mucho más alta que ancha (por ejemplo un ticket largo)."""
    try:
        ancho, alto = _abrir(contenido).size
    except Exception:
        return False
    return alto > ancho * LARGA


def _jpg(img: Image.Image) -> bytes:
    salida = io.BytesIO()
    img.save(salida, format="JPEG", quality=88)
    return salida.getvalue()


def _tramos(img: Image.Image) -> list[Image.Image]:
    """Corta una tira larga en pedazos de arriba hacia abajo que se pisan un poco."""
    if img.width > ANCHO_TRAMO:
        img = img.resize((ANCHO_TRAMO, round(img.height * ANCHO_TRAMO / img.width)), Image.LANCZOS)
    alto_tramo = min(LADO_IA, PIXELES_IA // img.width)
    paso = int(alto_tramo * (1 - SOLAPE))
    cantidad = max(1, -(-(img.height - alto_tramo) // paso) + 1)
    if cantidad > MAX_TRAMOS:  # tira larguísima: se achica un poco para no pasar el tope
        escala = MAX_TRAMOS / cantidad
        img = img.resize((max(1, round(img.width * escala)), round(img.height * escala)), Image.LANCZOS)
        return _tramos_fijos(img, alto_tramo, MAX_TRAMOS)
    return _tramos_fijos(img, alto_tramo, cantidad)


def _tramos_fijos(img: Image.Image, alto_tramo: int, cantidad: int) -> list[Image.Image]:
    alto_tramo = min(alto_tramo, img.height)
    if cantidad == 1:
        return [img.crop((0, 0, img.width, alto_tramo))]
    paso = (img.height - alto_tramo) / (cantidad - 1)  # repartidos parejo, el último toca el final
    return [img.crop((0, round(i * paso), img.width, round(i * paso) + alto_tramo)) for i in range(cantidad)]


def _preparar_imagenes(contenido: bytes, media_type: str) -> list[tuple[bytes, str]]:
    """Lo que se manda a la IA. Los PDF van tal cual. Las fotos grandes se achican para que el
    envío sea rápido y barato; las tiras largas (tickets) se cortan en tramos para que cada
    tramo se lea en tamaño completo."""
    if media_type == "application/pdf":
        return [(contenido, media_type)]
    try:
        img = _abrir(contenido)
        if img.height > img.width * LARGA and img.height > LADO_IA:
            return [(_jpg(t), "image/jpeg") for t in _tramos(img)]
        img.thumbnail((2000, 2000))
        return [(_jpg(img), "image/jpeg")]
    except Exception as exc:  # imagen dañada o formato raro
        raise ErrorLectura("No pude abrir la imagen. Probá mandar la foto otra vez.") from exc


def _texto_tramos(cantidad: int) -> str:
    if cantidad == 1:
        return ""
    return (f"\n\nLa factura es larga y viene en {cantidad} imágenes: son partes seguidas de la MISMA "
            "factura, de arriba hacia abajo (la 1 es el principio). Cada parte se pisa un poco con la "
            "siguiente: un renglón que aparece al final de una parte y otra vez al principio de la "
            "siguiente es el mismo renglón y va una sola vez. Sacá todos los renglones de todas las "
            "partes, sin saltear ninguno. El proveedor, el número y la fecha suelen estar en la "
            "primera parte; el total y el CDC, en la última.")


def _bloque_archivo(contenido: bytes, media_type: str) -> dict:
    datos = base64.standard_b64encode(contenido).decode("utf-8")
    tipo = "document" if media_type == "application/pdf" else "image"
    return {"type": tipo, "source": {"type": "base64", "media_type": media_type, "data": datos}}


def _texto_catalogo(catalogo: list[tuple[str, str]] | None) -> str:
    if not catalogo:
        return "\n\nTodavía no hay productos cargados en el inventario."
    lineas = "\n".join(f"{codigo}: {nombre}" for codigo, nombre in catalogo)
    return f"\n\nProductos que ya existen en el inventario (código: nombre):\n{lineas}"


async def extraer(contenido: bytes, media_type: str = "image/jpeg",
                  catalogo: list[tuple[str, str]] | None = None) -> dict:
    """Devuelve {es_factura, proveedor, nro_factura, fecha, total_factura, cdc, items:[{descripcion,
    cantidad, precio_unitario, total, codigo_existente}]}. catalogo: [(código, nombre)] de los productos."""
    if not config.ANTHROPIC_API_KEY:
        raise ErrorLectura("Falta configurar ANTHROPIC_API_KEY para poder leer facturas.")
    imagenes = _preparar_imagenes(contenido, media_type)
    bloques = []
    for n, (datos_img, tipo) in enumerate(imagenes, 1):
        if len(imagenes) > 1:
            bloques.append({"type": "text", "text": f"Parte {n} de {len(imagenes)}:"})
        bloques.append(_bloque_archivo(datos_img, tipo))
    texto = INSTRUCCIONES + _texto_tramos(len(imagenes)) + _texto_catalogo(catalogo)
    if len(imagenes) > 1:
        log.info("Factura larga: se manda en %d tramos", len(imagenes))
    cliente = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        respuesta = await cliente.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
            messages=[{"role": "user", "content": bloques + [{"type": "text", "text": texto}]}],
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
            "codigo_existente": (item.get("codigo_existente") or "").strip().upper(),
        })
    return {
        "es_factura": bool(datos.get("es_factura", True)),
        "proveedor": (datos.get("proveedor") or "").strip(),
        "nro_factura": (datos.get("nro_factura") or "").strip(),
        "fecha": (datos.get("fecha") or "").strip(),
        "total_factura": float(datos.get("total_factura") or 0),
        "cdc": re.sub(r"\D", "", datos.get("cdc") or ""),
        "items": items,
    }

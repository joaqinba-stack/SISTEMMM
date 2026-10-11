"""Impresión directa en impresoras térmicas de etiquetas (Xprinter, TSC y similares, lenguaje TSPL).

La impresora se conecta por USB a la computadora donde corre el sistema y se instala con su
driver. En el archivo .env se pone su nombre (IMPRESORA_NOMBRE) tal como aparece en
"Impresoras" del sistema operativo. Si estuviera en la red, se pone IMPRESORA_IP.
"""
import socket
import subprocess
import sys
import textwrap

from app import codigos, config
from app.etiqueta_diseno import Caja, diseno_etiqueta, precio_texto, tamano

PUNTOS_POR_MM = 8  # impresoras de 203 dpi
# Letras que trae la impresora: nombre -> (ancho, alto) de cada letra en puntos
LETRAS = {"1": (8, 12), "2": (12, 20), "3": (16, 24), "4": (24, 32), "5": (32, 48)}


class ErrorImpresora(Exception):
    """Mensaje claro para mostrar a la persona."""


def configurada() -> bool:
    return bool(config.IMPRESORA_NOMBRE or config.IMPRESORA_IP)


def _puntos(mm: float) -> int:
    return int(round(mm * PUNTOS_POR_MM))


def _ascii(texto: str) -> str:
    """Las letras de la impresora no tienen tildes ni ñ."""
    limpio = codigos.sin_acentos(str(texto)).replace('"', "'")
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in limpio)


def _elegir_letra(alto_mm: float, texto: str, ancho_mm: float) -> str:
    """La letra más grande que no supera el alto de la zona y entra a lo ancho."""
    alto, ancho = _puntos(alto_mm) * 1.25, _puntos(ancho_mm)
    candidatas = [k for k, (w, h) in LETRAS.items() if h <= alto and len(texto) * w <= ancho]
    return candidatas[-1] if candidatas else "1"


def _texto(caja: Caja, texto: str, alto_mm: float, alinear: str = "centro") -> str:
    letra = _elegir_letra(alto_mm, texto, caja.ancho)
    w, h = LETRAS[letra]
    largo = len(texto) * w
    x = _puntos(caja.x)
    if alinear == "centro":
        x += max(0, (_puntos(caja.ancho) - largo) // 2)
    elif alinear == "derecha":
        x += max(0, _puntos(caja.ancho) - largo)
    y = _puntos(caja.abajo) - h
    return f'TEXT {x},{max(0, y)},"{letra}",0,1,1,"{texto}"'


def _nombre(d, nombre: str) -> list[str]:
    letra = _elegir_letra(d.alto_linea * 0.9, "X", d.nombre.ancho)
    w, _ = LETRAS[letra]
    por_renglon = max(4, _puntos(d.nombre.ancho) // w)
    renglones = textwrap.wrap(nombre, por_renglon) or [""]
    if len(renglones) > d.lineas_nombre:
        renglones = renglones[: d.lineas_nombre]
        renglones[-1] = renglones[-1][: por_renglon - 3].rstrip() + "..."
    return [_texto(Caja(d.nombre.x, d.nombre.y + i * d.alto_linea, d.nombre.ancho, d.alto_linea), r,
                   d.alto_linea * 0.9) for i, r in enumerate(renglones)]


def tspl_etiquetas(pedidos: list[tuple[dict, int]], tamano_clave: str, con_precio: bool = True) -> bytes:
    """pedidos: [(producto, cantidad_de_copias), ...] -> comandos TSPL listos para mandar."""
    ancho, alto = tamano(tamano_clave)
    lineas = [
        f"SIZE {ancho} mm,{alto} mm",
        f"GAP {config.ETIQUETA_SEPARACION_MM:g} mm,0 mm",
        "DIRECTION 1,0",
        "REFERENCE 0,0",
        "DENSITY 8",
        "SPEED 4",
        "SET TEAR ON",
    ]
    for producto, copias in pedidos:
        if copias <= 0:
            continue
        codigo = _ascii(producto["codigo_barra"])
        d = diseno_etiqueta(ancho, alto, codigo, con_precio)
        lineas.append("CLS")
        lineas.extend(_nombre(d, _ascii(producto["nombre_interno"])))
        modulo = max(1, _puntos(d.modulo))
        lineas.append(f'BARCODE {_puntos(d.barras.x)},{_puntos(d.barras.y)},"128",{_puntos(d.barras.alto)},'
                      f'0,0,{modulo},{modulo},"{codigo}"')
        alinear_numero = "izquierda" if d.precio_a_la_derecha and d.precio else "centro"
        lineas.append(_texto(d.numero, codigo, d.numero.alto, alinear_numero))
        if d.precio:
            lineas.append(_texto(d.precio, precio_texto(producto["precio_venta"]), d.precio.alto,
                                 "derecha" if d.precio_a_la_derecha else "centro"))
        if d.codigo:
            lineas.append(_texto(d.codigo, _ascii(producto["codigo"]), d.codigo.alto))
        lineas.append(f"PRINT 1,{int(copias)}")
    return ("\r\n".join(lineas) + "\r\n").encode("ascii")


def agrupar(productos: list) -> list[tuple[dict, int]]:
    """[a, a, a, b] -> [(a, 3), (b, 1)] para mandar menos datos a la impresora."""
    pedidos: list[list] = []
    for p in productos:
        if pedidos and pedidos[-1][0]["id"] == p["id"]:
            pedidos[-1][1] += 1
        else:
            pedidos.append([p, 1])
    return [(p, n) for p, n in pedidos]


def enviar(datos: bytes) -> None:
    """Manda los comandos a la impresora (por red, por Windows o por CUPS en Linux/Mac)."""
    if config.IMPRESORA_IP:
        try:
            with socket.create_connection((config.IMPRESORA_IP, 9100), timeout=8) as s:
                s.sendall(datos)
        except OSError as exc:
            raise ErrorImpresora(f"No pude conectarme a la impresora en {config.IMPRESORA_IP}. "
                                 "Revisá que esté prendida y en la misma red.") from exc
        return
    nombre = config.IMPRESORA_NOMBRE
    if not nombre:
        raise ErrorImpresora("Falta configurar la impresora: poné su nombre en IMPRESORA_NOMBRE "
                             "en el archivo .env.")
    if sys.platform == "win32":
        try:
            import win32print
        except ImportError as exc:
            raise ErrorImpresora("Falta instalar pywin32 (pip install pywin32).") from exc
        try:
            impresora = win32print.OpenPrinter(nombre)
        except Exception as exc:
            raise ErrorImpresora(f"No encontré la impresora «{nombre}». Revisá que el nombre en .env "
                                 "sea igual al que aparece en Impresoras de Windows.") from exc
        try:
            win32print.StartDocPrinter(impresora, 1, ("Etiquetas", None, "RAW"))
            try:
                win32print.StartPagePrinter(impresora)
                win32print.WritePrinter(impresora, datos)
                win32print.EndPagePrinter(impresora)
            finally:
                win32print.EndDocPrinter(impresora)
        finally:
            win32print.ClosePrinter(impresora)
        return
    try:
        resultado = subprocess.run(["lp", "-d", nombre, "-o", "raw"], input=datos,
                                   capture_output=True, timeout=20)
    except FileNotFoundError as exc:
        raise ErrorImpresora("No encontré el sistema de impresión (CUPS) en esta computadora.") from exc
    if resultado.returncode != 0:
        raise ErrorImpresora(f"No pude imprimir en «{nombre}»: "
                             f"{resultado.stderr.decode(errors='ignore').strip() or 'error desconocido'}")


def imprimir(productos: list, tamano_clave: str, con_precio: bool = True) -> int:
    """Imprime una etiqueta por elemento de la lista. Devuelve cuántas se mandaron."""
    if not productos:
        raise ErrorImpresora("No hay etiquetas para imprimir.")
    enviar(tspl_etiquetas(agrupar(productos), tamano_clave, con_precio))
    return len(productos)

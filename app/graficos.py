"""Gráfico de línea del costo por unidad de un producto (SVG hecho en el servidor)."""
import html
import math
from datetime import date

from app import inventario as inv

ANCHO, ALTO = 720, 300
IZQ, DER, ARRIBA, ABAJO = 84, 28, 34, 40


def _pasos_lindos(minimo: float, maximo: float, cantidad: int = 4) -> list[float]:
    """Marcas del eje con números redondos (1, 2, 2,5 o 5 por potencia de 10)."""
    if maximo <= minimo:
        maximo = minimo + max(1.0, abs(minimo) * 0.1)
    bruto = (maximo - minimo) / cantidad
    potencia = 10 ** math.floor(math.log10(bruto))
    paso = next(m * potencia for m in (1, 2, 2.5, 5, 10) if m * potencia >= bruto)
    inicio = math.floor(minimo / paso) * paso
    marcas, valor = [], inicio
    while valor <= maximo + paso * 0.001:
        marcas.append(valor)
        valor += paso
    if marcas[-1] < maximo:
        marcas.append(marcas[-1] + paso)
    return marcas


def resumen_costos(puntos: list[dict]) -> dict | None:
    if not puntos:
        return None
    costos = [p["costo"] for p in puntos]
    primero, ultimo = costos[0], costos[-1]
    return {
        "primero": primero, "ultimo": ultimo, "minimo": min(costos), "maximo": max(costos),
        "variacion": ultimo - primero,
        "variacion_pct": (ultimo - primero) / primero * 100 if primero else 0,
        "compras": len(puntos),
    }


def linea_costos(puntos: list[dict]) -> str:
    """puntos: [{fecha 'AAAA-MM-DD', costo, proveedor, factura}] ordenados por fecha."""
    if len(puntos) < 2:
        return ""
    dias = [date.fromisoformat(p["fecha"][:10]).toordinal() for p in puntos]
    d0, d1 = min(dias), max(dias)
    marcas = _pasos_lindos(min(p["costo"] for p in puntos), max(p["costo"] for p in puntos))
    y0, y1 = marcas[0], marcas[-1]
    ancho_util, alto_util = ANCHO - IZQ - DER, ALTO - ARRIBA - ABAJO

    def x_de(i: int) -> float:
        if d1 == d0:  # todas el mismo día: se reparten parejo
            return IZQ + ancho_util * (i / (len(puntos) - 1))
        return IZQ + ancho_util * (dias[i] - d0) / (d1 - d0)

    def y_de(costo: float) -> float:
        return ARRIBA + alto_util * (1 - (costo - y0) / (y1 - y0))

    partes = [f'<svg class="grafico-linea" viewBox="0 0 {ANCHO} {ALTO}" role="img" '
              f'aria-label="Costo por unidad en cada compra">']
    for valor in marcas:
        y = y_de(valor)
        partes.append(f'<line class="grilla" x1="{IZQ}" x2="{ANCHO - DER}" y1="{y:.1f}" y2="{y:.1f}"/>')
        partes.append(f'<text class="eje" x="{IZQ - 10}" y="{y + 4:.1f}" text-anchor="end">'
                      f'{html.escape(inv.guaranies(valor))}</text>')
    # fechas: la primera, la última y hasta dos en el medio
    indices = sorted({0, len(puntos) - 1, *(round(k * (len(puntos) - 1) / 3) for k in (1, 2))})
    usadas: list[float] = []
    for i in indices:
        x = x_de(i)
        if any(abs(x - u) < 70 for u in usadas):
            continue
        usadas.append(x)
        ancla = "start" if i == 0 else ("end" if i == len(puntos) - 1 else "middle")
        partes.append(f'<text class="eje" x="{x:.1f}" y="{ALTO - 12}" text-anchor="{ancla}">'
                      f'{inv.fecha_txt(puntos[i]["fecha"])}</text>')

    coordenadas = [(x_de(i), y_de(p["costo"])) for i, p in enumerate(puntos)]
    partes.append('<polyline class="linea" points="' +
                  " ".join(f"{x:.1f},{y:.1f}" for x, y in coordenadas) + '"/>')

    costos = [p["costo"] for p in puntos]
    destacados = {0, len(puntos) - 1, costos.index(min(costos)), costos.index(max(costos))}
    for i, (p, (x, y)) in enumerate(zip(puntos, coordenadas)):
        info = (f'{inv.fecha_txt(p["fecha"])} · {p["proveedor"] or "sin proveedor"}'
                f'{" · factura " + p["factura"] if p["factura"] else ""} · {inv.guaranies(p["costo"])}')
        partes.append(f'<circle class="punto" cx="{x:.1f}" cy="{y:.1f}" r="5" tabindex="0" '
                      f'data-info="{html.escape(info)}"><title>{html.escape(info)}</title></circle>')
        if i in destacados:
            ancla = "start" if x < IZQ + 40 else ("end" if x > ANCHO - DER - 40 else "middle")
            arriba = y - 12 if y - 12 > ARRIBA - 6 else y + 22
            partes.append(f'<text class="valor" x="{x:.1f}" y="{arriba:.1f}" text-anchor="{ancla}">'
                          f'{html.escape(inv.guaranies(p["costo"]))}</text>')
    partes.append("</svg>")
    return "".join(partes)


def puntos_de_compras(filas) -> list[dict]:
    """Filas de listar_compras (más nuevas primero) -> puntos ordenados por fecha."""
    return [{"fecha": f["fecha"], "costo": f["costo_unitario"], "proveedor": f["proveedor"],
             "factura": f["nro_factura"]}
            for f in sorted(filas, key=lambda f: (f["fecha"], f["lote_id"]))]

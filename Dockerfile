FROM python:3.12-slim

# La hora de las ventas y del registro de actividad sale del reloj del contenedor.
ENV TZ=America/Asuncion \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATOS_DIR=/app/datos

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY run.py .

# Los datos (base, fotos y facturas) viven en un volumen, fuera de la imagen.
RUN useradd --create-home inventario && mkdir -p /app/datos && chown inventario:inventario /app/datos
USER inventario
VOLUME /app/datos

EXPOSE 8000
# Solo se llega al contenedor a través de Caddy, por eso se confía en sus encabezados.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

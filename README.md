# 📦 Sistema de Inventario

Control de stock simple, pensado para que cualquiera lo pueda usar, con:

- **Página web** con letra grande y botones grandes (funciona en la compu y en el celular).
- **Bot de Telegram**: mandás la **foto de una factura** y la compra se carga sola.
- **Códigos automáticos**: código de producto (`P-00001`) y
  **código de barra** por categoría (Vela `2002…`, Jabón `3003…`, Yeso `4004…`, Resina `5005…`, Varios `1001…`).
- **FIFO**: cada venta descuenta primero lo que se compró antes, y calcula la ganancia real.
- **Precios por porcentaje**: el precio de venta se sugiere con +40% y el % se cambia producto por
  producto desde la tabla de productos.
- **Ventas con varios productos** y **clientes** (se buscan por nombre, teléfono o RUC/CI, y se crean
  desde la misma venta).
- **Etiquetas con código de barra** para **impresora térmica** (Xprinter, TSC…) en 5 tamaños de rollo
  (55×28, 50×30, 60×40, 40×40, 55×45 mm): todas las de una factura (una por unidad) o las de un producto.
- **Corregir a la IA**: si una factura crea un producto que ya existía, se corrige en Telegram antes de
  guardar o después con «🔁 Es el mismo que…», y el sistema aprende ese nombre.
- **Panel de compras** con filtros por proveedor, producto y fechas, gráfico de cómo varió el costo de un
  producto, y **pasar a Excel**.
- El stock en pantalla **se actualiza solo** cada pocos segundos.

## Pantallas

| Pantalla | Qué hace |
|---|---|
| 🔔 Notificaciones | Productos con poco stock y productos nuevos para revisar. Queda vacía cuando todo está atendido |
| 📦 Productos y stock | Código, foto, nombres, dónde fue la última compra, % de ganancia y precio de venta (uno calcula el otro), stock, activo sí/no. ✨ Nuevo producto, Editar (y «Es el mismo que…»), Excel, imprimir códigos de barra |
| 🧾 Compras | Código, factura, proveedor, producto, precio unitario y total. Editar, Excel y 📊 Panel de compras (con gráfico del costo) |
| 💰 Vender | Uno o varios productos, cliente (buscar o crear) y forma de pago. Descuenta stock por FIFO |

## Instalación (una sola vez)

Se necesita **Python 3.10 o más nuevo**.

```bash
pip install -r requirements.txt
cp .env.example .env        # en Windows: copy .env.example .env
```

Abrí el archivo `.env` con el Bloc de notas y completá los datos (ver abajo).

### Cargar los datos del Excel anterior (opcional)

```bash
python scripts/importar_excel.py "Datos_Joaquín.xlsx"
```

- La hoja **Producto** carga los productos con su código de barra y precio (el SKU del Excel solo se usa para unir las hojas). El *Stock disponible* queda como stock inicial.
- La hoja **Compras** queda como historial. Los productos que no estaban en la hoja Producto se crean y quedan marcados **para revisar**.
- La hoja **Ventas** queda como historial.

Al terminar, el script muestra un resumen con lo que hay que revisar.

## Arrancar el sistema

```bash
python run.py
```

Abrí **http://localhost:8000** en el navegador. Desde el celular, en la misma red WiFi, usá
`http://IP-DE-LA-COMPU:8000`.

## Configurar el bot de Telegram

1. En Telegram buscá **@BotFather**, escribile `/newbot` y seguí los pasos. Te da un **token**.
   Pegalo en `.env` en `TELEGRAM_TOKEN=`.
2. Conseguí una clave de **Anthropic** en <https://console.anthropic.com> y pegala en
   `ANTHROPIC_API_KEY=`. Es la que usa Claude para leer las fotos de las facturas.
3. Arrancá el sistema (`python run.py`), abrí tu bot en Telegram y escribile `/miid`.
   Te responde tu número de usuario. Ponelo en `TELEGRAM_USUARIOS_PERMITIDOS=`. Si son varias personas, separá los números con comas.
4. Volvé a arrancar el sistema.

### Cómo se usa el bot

1. Sacale una foto a la factura (de cerca, con buena luz) y mandásela al bot. También acepta PDF.
2. El bot responde con lo que leyó: proveedor, número de factura, fecha y cada producto.
   - ✔️ = producto que ya existe (le suma stock)
   - 🆕 = producto nuevo. Se crea solo con sus códigos y precio +40%, y queda marcado para revisar en la página
3. Si un producto está mal, tocá **✏️ Corregir** con su número, escribí parte del nombre correcto y
   elegilo de la lista (o «Es un producto nuevo»). El sistema aprende ese nombre para la próxima vez.
4. Si está todo bien, tocá **✅ Sí, guardar**. Si no, tocá **❌ No, cancelar**.
4. Si la factura ya se había cargado antes, el bot avisa.

Otros comandos: `/stock nombre` muestra el stock de un producto, y `/ayuda` muestra las instrucciones.

## Productos y proveedores

Un producto es uno solo aunque se compre en distintos lugares: el proveedor queda guardado en
cada compra (y en el Panel de compras se puede comparar cuánto costó en cada uno). Cuando llega
una factura, la IA recibe la lista de productos y reconoce si es uno que ya existe aunque esté
escrito distinto o venga de otro proveedor; así no se crean códigos repetidos.

## Impresora térmica de etiquetas

Funciona con impresoras de etiquetas que usan el lenguaje TSPL (Xprinter, TSC, HPRT, Beeprt y la
mayoría de las genéricas) con rollos de etiquetas adhesivas térmicas.

1. Conectá la impresora por **USB** a la computadora donde corre el sistema e instalá su driver.
2. Poné el rollo. En la página **Imprimir códigos de barra** elegí el tamaño del rollo
   (55×28, 50×30, 60×40, 40×40 o 55×45 mm). Se recuerda para la próxima vez.
3. En el archivo `.env` escribí el nombre de la impresora tal como aparece en *Impresoras* de Windows:
   `IMPRESORA_NOMBRE=Xprinter XP-365B`. Volvé a abrir el sistema.
4. Tocá **🧪 Imprimir etiqueta de prueba**. Si sale bien, ya está.

Desde ahí:
- **🖨️ Imprimir** en una factura imprime una etiqueta por cada unidad comprada.
- **🖨️ Imprimir** en un producto imprime la cantidad que elijas.
- Después de guardar una factura en Telegram, el bot ofrece **🏷️ Imprimir etiquetas de esta factura**.
- **⬇️ PDF** descarga un PDF con una etiqueta por página del tamaño del rollo (para imprimir con la
  ventana de impresión si hace falta), y también hay una hoja A4 de 21 etiquetas para impresoras comunes.

En Windows hace falta `pywin32` (se instala solo con `pip install -r requirements.txt`). En Linux o Mac
se usa CUPS (`lp`). Si la impresora está en la red, poné su IP en `IMPRESORA_IP` en lugar del nombre.

## Configuración (`.env`)

| Variable | Para qué sirve |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot (de @BotFather). Si está vacío, el bot no arranca |
| `TELEGRAM_USUARIOS_PERMITIDOS` | Números de usuario que pueden usar el bot |
| `ANTHROPIC_API_KEY` | Clave para leer facturas con Claude |
| `CLAUDE_MODEL` | Modelo de Claude (por defecto `claude-opus-5-5`) |
| `CLAVE_ACCESO` | Clave para entrar a la página. Si está vacía, no la pide |
| `URL_PUBLICA` | Dirección de la página, para que el bot mande el link |
| `MARGEN_POR_DEFECTO` | % de ganancia sugerido (40) |
| `STOCK_BAJO` | Desde qué cantidad se avisa "queda poco stock" (2) |
| `IMPRESORA_NOMBRE` | Nombre de la impresora térmica (USB) |
| `IMPRESORA_IP` | IP de la impresora, solo si está en la red |
| `ETIQUETA_TAMANO` | Tamaño de etiqueta inicial: `55x28`, `50x30`, `60x40`, `40x40` o `55x45` |
| `ETIQUETA_SEPARACION_MM` | Espacio entre etiquetas del rollo (2) |

Los datos (base de datos, fotos de productos y facturas) se guardan en la carpeta `datos/`.
**Hacé una copia de esa carpeta seguido.**

## Para programadores

- `app/main.py` contiene las rutas web (FastAPI + plantillas Jinja2).
- `app/inventario.py` tiene las reglas: productos, compras (lotes), ventas FIFO y precios.
- `app/codigos.py` genera el código del producto y el código de barra.
- `app/telegram_bot.py` y `app/factura_ia.py` son el bot y la lectura de facturas con Claude.
- `app/db.py` define el esquema SQLite. El stock es la suma de `lotes.cantidad_restante`.

Para correr las pruebas:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

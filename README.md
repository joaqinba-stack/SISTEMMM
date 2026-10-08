# 📦 Sistema de Inventario

Control de stock simple, pensado para que cualquiera lo pueda usar, con:

- **Página web** con letra grande y botones grandes (funciona en la compu y en el celular).
- **Bot de Telegram**: mandás la **foto de una factura** y la compra se carga sola.
- **Códigos automáticos**: código de producto (`P-00001`), **SKU** (`MOL-VEL-ROS-00001`) y
  **código de barra** por categoría (Vela `2002…`, Jabón `3003…`, Yeso `4004…`, Resina `5005…`, Varios `1001…`).
- **FIFO**: cada venta descuenta primero lo que se compró antes, y calcula la ganancia real.
- **Precios por porcentaje**: el precio de venta se sugiere con +40% (se puede cambiar), y se pueden
  cambiar todos los precios de una categoría con un porcentaje.
- **Imprimir** y **pasar a Excel** los productos, las compras y las ventas.
- El stock en pantalla **se actualiza solo** cada pocos segundos.

## Pantallas

| Pantalla | Qué hace |
|---|---|
| 🏠 Inicio | Botones grandes, productos para revisar y productos con poco stock |
| 🏷️ Nuevo producto | Carga el producto y su primera compra. Calcula el precio por unidad y el precio de venta |
| 📦 Productos y stock | SKU, código, código de barra, foto, nombres, proveedor, precio, stock, activo sí/no. Editar, imprimir, Excel, cambiar precios por % |
| 🧾 Compras | Código, factura, proveedor, producto, precio unitario y total. Editar, imprimir, Excel, agregar compra |
| 💰 Vender | Registra una venta y descuenta stock por FIFO |

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

- La hoja **Producto** carga los productos con su SKU, código de barra y precio. El *Stock disponible* queda como stock inicial.
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
3. Si está bien, tocá **✅ Sí, guardar**. Si no, tocá **❌ No, cancelar**.
4. Si la factura ya se había cargado antes, el bot avisa.

Otros comandos: `/stock nombre` muestra el stock de un producto, y `/ayuda` muestra las instrucciones.

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

Los datos (base de datos, fotos de productos y facturas) se guardan en la carpeta `datos/`.
**Hacé una copia de esa carpeta seguido.**

## Para programadores

- `app/main.py` contiene las rutas web (FastAPI + plantillas Jinja2).
- `app/inventario.py` tiene las reglas: productos, compras (lotes), ventas FIFO y precios.
- `app/codigos.py` genera el SKU, el código y el código de barra.
- `app/telegram_bot.py` y `app/factura_ia.py` son el bot y la lectura de facturas con Claude.
- `app/db.py` define el esquema SQLite. El stock es la suma de `lotes.cantidad_restante`.

Para correr las pruebas:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

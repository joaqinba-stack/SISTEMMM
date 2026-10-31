# 📦 Sistema de Inventario

Control de stock simple, pensado para que cualquiera lo pueda usar, con:

- **Página web** con letra grande y botones grandes (funciona en la compu y en el celular).
- **Bot de Telegram**: mandás la **foto de una factura** y la compra se carga sola.
- **Códigos automáticos**: cada producto recibe su código (`P-00001`), que también es su
  **código de barra**: es lo que se imprime en la etiqueta y lo que lee el lector.
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

- La hoja **Producto** carga los productos con su precio (el SKU y el código de barra viejo del Excel no se usan: el código de barra pasa a ser el código P-00001). El *Stock disponible* queda como stock inicial.
- La hoja **Compras** queda como historial. Los productos que no estaban en la hoja Producto se crean y quedan marcados **para revisar**.
- La hoja **Ventas** queda como historial.

Al terminar, el script muestra un resumen con lo que hay que revisar.

## Arrancar el sistema

```bash
python run.py
```

Abrí **http://localhost:8000** en el navegador. Desde el celular, en la misma red WiFi, usá
`http://IP-DE-LA-COMPU:8000`.

## Instalar en un servidor (VPS)

Para que el sistema esté siempre prendido y se entre con una dirección propia. Alcanza con un
servidor chico (1 vCPU y 2 GB de memoria) con Ubuntu y Docker.

1. Hacé que un nombre apunte a la IP del servidor (un registro **A** en tu dominio) y ponelo en
   `DOMINIO`. Si todavía no hay dominio, poné en `DOMINIO` la IP pública del servidor y
   `CERTIFICADO=shortlived`: se entra por `https://LA-IP` con candado igual.
2. En el servidor:

   ```bash
   git clone https://github.com/joaqinba-stack/SISTEMMM.git inventario
   cd inventario
   cp .env.example .env
   nano .env            # completar DOMINIO (y CERTIFICADO), TELEGRAM_TOKEN y ANTHROPIC_API_KEY
   docker compose up -d --build
   ```

3. Abrí `https://TU-DOMINIO` y **creá enseguida la primera cuenta**: es la del administrador.

Caddy pide y renueva solo el certificado (el candado). Hacen falta abiertos los puertos 80 y 443.

- **Actualizar** a una versión nueva: `git pull && docker compose up -d --build`
- **Ver qué pasa:** `docker compose logs -f app`
- **Copia de seguridad** de la base, las fotos y las facturas (hacela seguido y guardala fuera del servidor):

  ```bash
  docker run --rm -v inventario_datos:/datos -v "$PWD":/copia alpine tar czf /copia/respaldo-$(date +%F).tgz -C /datos .
  ```

- **Rescatar una cuenta:** `docker compose exec app python scripts/restablecer_acceso.py USUARIO --admin`
- **El bot de Telegram** tiene que correr en un solo lugar: si se prende en el servidor, hay que
  apagarlo en la computadora (el mismo token no funciona en dos lados a la vez).
- **La impresora de etiquetas** se conecta a la computadora desde la que se entra a la página
  (con su driver instalado). El botón **🖨️ Imprimir** abre la ventana de impresión del navegador
  con una etiqueta por página del tamaño exacto del rollo. La primera vez hay que elegir ahí la
  impresora, el tamaño de papel de la etiqueta, sin márgenes y escala 100%. El botón de imprimir
  del bot de Telegram solo aparece cuando la impresora está conectada al propio servidor.

## Cuentas de usuario

Para entrar hace falta una cuenta con **usuario y contraseña**. En la pantalla de entrada está
**Olvidé mi contraseña**.

- **La primera cuenta es la del administrador**: en una instalación nueva, la pantalla de entrada
  ofrece crearla. Creala apenas arranques el sistema por primera vez. Después ya nadie puede
  crearse una cuenta solo.
- **Las demás cuentas las crea el administrador** en **👤 (tu nombre) → Usuarios → Nuevo usuario**,
  con una **contraseña temporal**. La persona la tiene que cambiar la primera vez que entra (hasta
  entonces no puede hacer otra cosa). En Usuarios también se bloquea una cuenta o se la hace administradora.
- **Qué puede hacer cada perfil:**
  - **Usuario:** vender, ver productos y compras, cargar productos nuevos **sin compra ni precio**
    (quedan con stock 0 y «para revisar»), editar nombre, categoría y foto, imprimir etiquetas y ver sus ventas.
  - **Administrador:** además, cambiar **precios, costos, cantidades y %**, **ajustar el stock**,
    corregir o borrar compras, unir productos repetidos, crear y bloquear cuentas y ver el registro de actividad.
- **El stock baja con ventas.** Fuera de eso, solo el administrador lo cambia, y todo queda en el
  registro de actividad:
  - **Cambiar el stock**: directo en la columna *En stock* de **📦 Productos y stock** (se escribe el
    número y se toca ✓), o en **Editar**, en el campo *En stock* (se guarda con 💾 Guardar), donde además se puede anotar el motivo
    (conteo, se rompió, se perdió…; si no se escribe queda «Corrección manual»). Si hay menos, se descuentan de las compras
    más viejas primero; si hay más, la diferencia entra como «📦 Ajuste de stock» en Compras, al costo
    de la última compra. La ficha muestra los últimos ajustes.
  - Corregir o borrar una compra mal cargada (mientras no hayan salido unidades de ella).
- **Recuperar la contraseña sin correo:** cuando la persona elige su contraseña, el sistema le
  muestra una sola vez un **código de recuperación** (por ejemplo `ABCD-EFGH-JKLM`). Hay que guardarlo.
  Con el usuario y ese código se elige una contraseña nueva en *Olvidé mi contraseña*, y se entrega
  un código nuevo. El administrador nunca ve ni la contraseña ni el código de los demás.
- Si alguien perdió también el código, el administrador le genera otro desde **Usuarios**.
  Si es el propio administrador el que quedó afuera, se rescata desde la computadora del sistema:
  `python scripts/restablecer_acceso.py USUARIO --admin`
- Después de 5 contraseñas (o códigos) errados seguidos la cuenta se frena 15 minutos.
- **Registro de actividad** (solo administradores): cada cosa que hace cada usuario queda anotada
  con fecha y hora: entradas, productos, precios, compras, ventas, etiquetas, descargas de Excel,
  cambios de cuentas y lo que se carga por Telegram.
- **Ventas por usuario**: cuánto vendió cada cuenta en el período elegido (cantidad de ventas,
  unidades, total y ganancia) con el detalle debajo. Quien no es administrador ve solo sus ventas.

## Configurar el bot de Telegram

1. En Telegram buscá **@BotFather**, escribile `/newbot` y seguí los pasos. Te da un **token**.
   Pegalo en `.env` en `TELEGRAM_TOKEN=`.
2. Conseguí una clave de **Anthropic** en <https://console.anthropic.com> y pegala en
   `ANTHROPIC_API_KEY=`. Es la que usa Claude para leer las fotos de las facturas.
3. Arrancá el sistema (`python run.py`), abrí tu bot en Telegram y escribile `/miid`.
   Te responde tu número de usuario. Ponelo en `TELEGRAM_USUARIOS_PERMITIDOS=`. Si son varias personas, separá los números con comas.
   Si lo dejás vacío, el bot es de uso libre y este paso no hace falta.
4. Volvé a arrancar el sistema.

### Cómo se usa el bot

1. Sacale una foto a la factura (de cerca, con buena luz) y mandásela al bot. También acepta PDF.
2. El bot responde con lo que leyó: proveedor, número de factura, fecha y cada producto.
   - ✔️ = producto que ya existe (le suma stock)
   - 🆕 = producto nuevo. Se crea solo con sus códigos y precio +40%, y queda marcado para revisar en la página
   - 🔁 = si un producto nuevo se parece a uno que ya existe, aparece un botón para elegirlo con un toque
   - Debajo de cada ✔️ se ve cómo figura en la factura, para notar si se eligió el producto equivocado
   - ⚠️ = los renglones no suman el total de la factura, o el número leído no coincide con el CDC
   - 🔐 = en facturas electrónicas, el número y la fecha se toman del CDC (los 44 dígitos)
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
| `TELEGRAM_USUARIOS_PERMITIDOS` | Números de usuario que pueden usar el bot. Si está vacío, el bot es de uso libre |
| `ANTHROPIC_API_KEY` | Clave para leer facturas con Claude |
| `CLAUDE_MODEL` | Modelo de Claude (por defecto `claude-opus-5-5`) |
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
- `app/codigos.py` genera el código del producto (que también es su código de barra).
- `app/telegram_bot.py` y `app/factura_ia.py` son el bot y la lectura de facturas con Claude.
- `app/usuarios.py` maneja las cuentas, la recuperación de contraseña y el registro de actividad.
- `app/db.py` define el esquema SQLite. El stock es la suma de `lotes.cantidad_restante`.

Para correr las pruebas:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

// Ayudas de la página: buscadores, carrito de venta, cálculos automáticos y stock que se actualiza solo.
(function () {
  "use strict";

  // '225.000' -> 225000 ; '2,5' -> 2.5 (igual que en el servidor)
  function leerNumero(texto) {
    if (texto === null || texto === undefined) return NaN;
    var limpio = String(texto).replace(/[^\d,.\-]/g, "");
    if (!limpio) return NaN;
    if (limpio.indexOf(",") >= 0) {
      limpio = limpio.replace(/\./g, "").replace(",", ".");
    } else if (limpio.indexOf(".") >= 0) {
      var partes = limpio.split(".");
      if (!(partes.length === 2 && partes[1].length <= 2)) limpio = limpio.replace(/\./g, "");
    }
    return parseFloat(limpio);
  }
  function miles(n) { return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "."); }
  function gs(n) { return isFinite(n) ? "₲ " + miles(n) : "—"; }
  function cant(n) {
    n = Math.round(n * 1000) / 1000;
    return Number.isInteger(n) ? miles(n) : String(n).replace(".", ",");
  }
  function norm(s) {
    return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  function esc(s) {
    return String(s === undefined || s === null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ------------------------------------------------------------------ buscador
  // Cuadro de texto con lista de sugerencias. Al elegir una opción la lista se cierra.
  var buscadores = {};
  function crearBuscador(raiz) {
    var entrada = raiz.querySelector("input[type=text]");
    var lista = raiz.querySelector(".sugerencias");
    var datos = JSON.parse(document.querySelector(raiz.dataset.buscador).textContent);
    var destino = raiz.dataset.destino ? document.querySelector(raiz.dataset.destino) : null;
    var visibles = [], activo = -1;
    var api = { elegido: null, alElegir: null, alCrear: null, entrada: entrada };

    function cerrar() { lista.hidden = true; entrada.setAttribute("aria-expanded", "false"); activo = -1; }
    function pintar() {
      var q = norm(entrada.value.trim());
      visibles = datos.filter(function (d) {
        return !q || norm(d.texto + " " + (d.detalle || "") + " " + (d.buscar || "")).indexOf(q) >= 0;
      }).slice(0, 40);
      var html = visibles.map(function (d, i) {
        return '<li role="option" id="' + lista.id + "-" + i + '" data-i="' + i + '" class="' + (i === activo ? "activa" : "") + '">' +
          (d.imagen !== undefined ? (d.imagen ? '<img class="foto-sugerencia" src="' + esc(d.imagen) + '" alt="" loading="lazy">'
                                                : '<span class="foto-sugerencia sin-foto">📷</span>') : "") +
          '<span class="texto-sugerencia"><b>' + esc(d.texto) + "</b>" + (d.detalle ? "<span>" + esc(d.detalle) + "</span>" : "") + "</span></li>";
      }).join("");
      if (raiz.dataset.crear && q) {
        html += '<li role="option" class="crear" data-crear="1">➕ Crear cliente nuevo «' + esc(entrada.value.trim()) + "»</li>";
      }
      if (!html) html = '<li class="nada">No se encontró nada con ese nombre.</li>';
      lista.innerHTML = html;
      lista.hidden = false;
      entrada.setAttribute("aria-expanded", "true");
      entrada.setAttribute("aria-activedescendant", activo >= 0 ? lista.id + "-" + activo : "");
    }
    function elegir(d) {
      api.elegido = d;
      entrada.value = d.texto;
      if (destino) destino.value = d.id;
      cerrar();
      if (api.alElegir) api.alElegir(d);
    }

    entrada.addEventListener("focus", pintar);
    entrada.addEventListener("click", pintar);
    entrada.addEventListener("input", function () {
      api.elegido = null;
      if (destino) destino.value = "";
      activo = -1;
      pintar();
    });
    entrada.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (lista.hidden) pintar();
        var total = visibles.length;
        if (!total) return;
        activo = e.key === "ArrowDown" ? (activo + 1) % total : (activo - 1 + total) % total;
        pintar();
        var li = lista.querySelector(".activa");
        if (li) li.scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (lista.hidden && api.elegido) {
          if (api.alElegir) api.alElegir(api.elegido);
          return;
        }
        if (activo >= 0 && visibles[activo]) elegir(visibles[activo]);
        else if (visibles.length === 1) elegir(visibles[0]);
        else if (raiz.dataset.crear && entrada.value.trim() && !visibles.length) {
          cerrar();
          if (api.alCrear) api.alCrear(entrada.value.trim());
        }
      } else if (e.key === "Escape") {
        cerrar();
      }
    });
    entrada.addEventListener("blur", function () { setTimeout(cerrar, 150); });
    lista.addEventListener("mousedown", function (e) { e.preventDefault(); });
    lista.addEventListener("click", function (e) {
      var li = e.target.closest("li");
      if (!li) return;
      if (li.dataset.crear) {
        cerrar();
        if (api.alCrear) api.alCrear(entrada.value.trim());
      } else if (li.dataset.i !== undefined) {
        elegir(visibles[+li.dataset.i]);
      }
    });

    api.elegir = elegir;
    api.agregar = function (d) { datos.push(d); };
    api.limpiar = function () {
      api.elegido = null;
      entrada.value = "";
      if (destino) destino.value = "";
      cerrar();
    };
    if (destino && destino.value) {
      api.elegido = datos.filter(function (d) { return String(d.id) === destino.value; })[0] || null;
    }
    return api;
  }
  document.querySelectorAll(".buscador[data-buscador]").forEach(function (raiz) {
    buscadores[raiz.id] = crearBuscador(raiz);
  });

  // ------------------------------------------------------------------ venta (carrito)
  var formVenta = document.querySelector("form[data-venta]");
  if (formVenta) {
    var bProducto = buscadores["buscador-producto"];
    var bCliente = buscadores["buscador-cliente"];
    var campoCantidad = document.getElementById("cantidad-producto");
    var errorProducto = document.getElementById("error-producto");
    var cuerpo = document.querySelector("#carrito tbody");
    var botonGuardar = document.getElementById("guardar-venta");
    var carrito = [];

    var mostrarError = function (texto) {
      errorProducto.textContent = texto;
      errorProducto.hidden = !texto;
    };
    var pintarCarrito = function () {
      var total = 0;
      cuerpo.innerHTML = carrito.length ? carrito.map(function (item, i) {
        var subtotal = item.cantidad * item.producto.precio;
        total += subtotal;
        return "<tr>" +
          '<td data-titulo="Producto"><div class="item-venta">' + (item.producto.imagen
            ? '<img class="foto-chica ampliable" src="' + esc(item.producto.imagen) + '" alt="' + esc(item.producto.texto) + '" tabindex="0">'
            : '<span class="foto-chica sin-foto">📷</span>') + "<b>" + esc(item.producto.texto) + "</b></div>" +
          '<input type="hidden" name="producto_id" value="' + item.producto.id + '">' +
          '<input type="hidden" name="cantidad" value="' + item.cantidad + '"></td>' +
          '<td data-titulo="Cantidad" class="num">' + cant(item.cantidad) + "</td>" +
          '<td data-titulo="Precio" class="num">' + gs(item.producto.precio) + "</td>" +
          '<td data-titulo="Subtotal" class="num"><b>' + gs(subtotal) + "</b></td>" +
          '<td><button class="boton chico peligro" type="button" data-quitar="' + i + '" aria-label="Quitar ' +
          esc(item.producto.texto) + '">🗑️ Quitar</button></td></tr>';
      }).join("") : '<tr class="carrito-vacio"><td colspan="5">Todavía no agregaste productos.</td></tr>';
      document.getElementById("total-venta").textContent = gs(total);
      botonGuardar.disabled = !carrito.length;
    };
    var agregar = function () {
      var producto = bProducto.elegido;
      if (!producto) return mostrarError("Elegí un producto de la lista.");
      var n = parseInt(campoCantidad.value, 10);
      if (!(n > 0)) return mostrarError("Escribí una cantidad mayor a cero.");
      var existente = carrito.filter(function (it) { return it.producto.id === producto.id; })[0];
      var nuevaCantidad = (existente ? existente.cantidad : 0) + n;
      if (nuevaCantidad > producto.stock + 1e-9) {
        return mostrarError("No alcanza: de «" + producto.texto + "» hay " + cant(producto.stock) + ".");
      }
      if (existente) existente.cantidad = nuevaCantidad;
      else carrito.push({ producto: producto, cantidad: n });
      mostrarError("");
      bProducto.limpiar();
      campoCantidad.value = 1;
      pintarCarrito();
      bProducto.entrada.focus();
    };
    bProducto.alElegir = function () { mostrarError(""); campoCantidad.focus(); campoCantidad.select(); };
    document.getElementById("agregar-producto").addEventListener("click", agregar);
    campoCantidad.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); agregar(); }
    });
    cuerpo.addEventListener("click", function (e) {
      var b = e.target.closest("[data-quitar]");
      if (!b) return;
      carrito.splice(+b.dataset.quitar, 1);
      pintarCarrito();
    });

    // Cliente: buscar, elegir o crear uno nuevo.
    var campoCliente = document.getElementById("cliente_id");
    var cajaBuscar = document.getElementById("buscador-cliente");
    var cajaElegido = document.getElementById("cliente-elegido");
    var cajaNuevo = document.getElementById("nuevo-cliente");
    var errorCliente = document.getElementById("nc-error");
    var mostrarCliente = function (d) {
      campoCliente.value = d ? d.id : "";
      document.getElementById("cliente-nombre").textContent = d ? d.texto : "";
      document.getElementById("cliente-detalle").textContent = d && d.detalle ? "· " + d.detalle : "";
      cajaElegido.hidden = !d;
      cajaBuscar.hidden = !!d;
      cajaNuevo.hidden = true;
    };
    bCliente.alElegir = mostrarCliente;
    bCliente.alCrear = function (texto) {
      document.getElementById("nc-nombre").value = texto;
      document.getElementById("nc-telefono").value = "";
      document.getElementById("nc-documento").value = "";
      errorCliente.hidden = true;
      cajaBuscar.hidden = true;
      cajaNuevo.hidden = false;
      document.getElementById("nc-telefono").focus();
    };
    document.getElementById("quitar-cliente").addEventListener("click", function () {
      mostrarCliente(null);
      bCliente.limpiar();
      bCliente.entrada.focus();
    });
    document.getElementById("nc-cancelar").addEventListener("click", function () {
      cajaNuevo.hidden = true;
      cajaBuscar.hidden = false;
      bCliente.entrada.focus();
    });
    document.getElementById("nc-guardar").addEventListener("click", function () {
      var boton = this;
      var datos = {
        nombre: document.getElementById("nc-nombre").value.trim(),
        telefono: document.getElementById("nc-telefono").value.trim(),
        documento: document.getElementById("nc-documento").value.trim()
      };
      if (!datos.nombre) {
        errorCliente.textContent = "Falta escribir el nombre del cliente.";
        errorCliente.hidden = false;
        return;
      }
      boton.disabled = true;
      fetch("/api/clientes", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos)
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          boton.disabled = false;
          if (!res.ok) throw new Error(res.j.error || "No se pudo guardar el cliente.");
          bCliente.agregar(res.j);
          bCliente.elegir(res.j);
        })
        .catch(function (err) {
          boton.disabled = false;
          errorCliente.textContent = err.message || "No se pudo guardar el cliente. Probá de nuevo.";
          errorCliente.hidden = false;
        });
    });
    formVenta.addEventListener("submit", function (e) {
      if (!carrito.length) { e.preventDefault(); mostrarError("Agregá al menos un producto a la venta."); }
    });
    pintarCarrito();
  }

  // ------------------------------------------------------------------ fotos que se agrandan
  // Al pasar el mouse por una foto se ve grande al lado; al tocarla o hacer clic se abre en pantalla completa.
  var vistaFlotante = document.createElement("img");
  vistaFlotante.className = "foto-flotante";
  vistaFlotante.alt = "";
  vistaFlotante.hidden = true;
  document.body.appendChild(vistaFlotante);
  var visor = document.createElement("div");
  visor.className = "visor-foto";
  visor.hidden = true;
  visor.setAttribute("role", "dialog");
  visor.setAttribute("aria-modal", "true");
  visor.innerHTML = '<figure><img alt=""><figcaption></figcaption></figure><button type="button" class="boton cerrar-visor">✖ Cerrar</button>';
  document.body.appendChild(visor);
  var cerrarVisor = function () { visor.hidden = true; };
  var abrirVisor = function (img) {
    vistaFlotante.hidden = true;
    visor.querySelector("img").src = img.currentSrc || img.src;
    visor.querySelector("figcaption").textContent = img.alt || "";
    visor.hidden = false;
    visor.querySelector(".cerrar-visor").focus();
  };
  visor.addEventListener("click", function (e) { if (!e.target.closest("img")) cerrarVisor(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !visor.hidden) cerrarVisor();
    if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("img.ampliable")) {
      e.preventDefault();
      abrirVisor(e.target);
    }
  });
  document.addEventListener("click", function (e) {
    var img = e.target.closest && e.target.closest("img.ampliable");
    if (img) { e.preventDefault(); abrirVisor(img); }
  });
  var puedeFlotar = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  document.addEventListener("mouseover", function (e) {
    var img = puedeFlotar && e.target.closest && e.target.closest("img.ampliable");
    if (!img || !visor.hidden) return;
    vistaFlotante.src = img.currentSrc || img.src;
    vistaFlotante.hidden = false;
    var r = img.getBoundingClientRect(), lado = 280, margen = 12;
    var x = r.right + margen + lado > window.innerWidth ? r.left - lado - margen : r.right + margen;
    var y = Math.min(Math.max(margen, r.top + r.height / 2 - lado / 2), window.innerHeight - lado - margen);
    vistaFlotante.style.left = Math.max(margen, x) + "px";
    vistaFlotante.style.top = y + "px";
  });
  document.addEventListener("mouseout", function (e) {
    if (e.target.closest && e.target.closest("img.ampliable")) vistaFlotante.hidden = true;
  });

  // ------------------------------------------------------------------ formularios con precios
  // Campos de dinero: al salir del campo se muestran con puntos de miles.
  document.querySelectorAll("input[data-dinero]").forEach(function (campo) {
    campo.addEventListener("blur", function () {
      var n = leerNumero(campo.value);
      if (isFinite(n)) campo.value = miles(n);
    });
    var n = leerNumero(campo.value);
    if (isFinite(n)) campo.value = miles(n);
  });

  // Precio unitario, % de ganancia y precio de venta (Nuevo producto y Editar).
  document.querySelectorAll("form[data-calculo-precio]").forEach(function (form) {
    var cantidad = form.querySelector("#cantidad");
    var costo = form.querySelector("#costo_total");
    var unitarioTxt = form.querySelector("#precio_unitario");
    var costoBase = form.querySelector("#costo_base");
    var margen = form.querySelector("#margen_pct");
    var precio = form.querySelector("#precio_venta");
    var precioEscritoAMano = precio && precio.value !== "";

    function unitario() {
      if (cantidad && costo) {
        var c = leerNumero(cantidad.value), t = leerNumero(costo.value);
        return c > 0 && t >= 0 ? t / c : NaN;
      }
      return costoBase ? parseFloat(costoBase.value) : NaN;
    }
    function recalcular() {
      var u = unitario();
      if (unitarioTxt) unitarioTxt.textContent = gs(u);
      if (!precio || !margen) return;
      var m = leerNumero(margen.value);
      if (!(u > 0) || !isFinite(m)) return;
      if (!precioEscritoAMano) precio.value = miles(Math.round(u * (1 + m / 100)));
    }
    [cantidad, costo].forEach(function (campo) {
      if (campo) campo.addEventListener("input", recalcular);
    });
    if (margen) margen.addEventListener("input", function () {
      precioEscritoAMano = false;
      recalcular();
    });
    if (precio) precio.addEventListener("input", function () {
      precioEscritoAMano = precio.value !== "";
      var u = unitario(), p = leerNumero(precio.value);
      if (margen && u > 0 && p > 0) margen.value = Math.round((p / u - 1) * 1000) / 10;
    });
    if (!costoBase) recalcular();
  });

  // Tabla de productos: el precio se puede poner por % de ganancia o escribirlo a mano.
  // Si se escribe el %, se calcula el precio; si se escribe el precio, se calcula el %.
  document.querySelectorAll("form.precio-form").forEach(function (form) {
    var costo = parseFloat(form.dataset.costo);
    var origen = form.querySelector("input[name=origen]");
    var margen = form.querySelector("input[data-campo=margen]");
    var precio = document.querySelector('input[form="' + form.id + '"][data-campo=precio]');
    var boton = document.querySelector('button[form="' + form.id + '"]');
    var marcar = function (cual) {
      origen.value = cual;
      boton.classList.add("principal");
      boton.title = "Tocá para guardar el cambio";
    };
    margen.addEventListener("input", function () {
      marcar("margen");
      var m = leerNumero(margen.value);
      if (costo > 0 && isFinite(m) && m >= 0) precio.value = miles(Math.round(costo * (1 + m / 100)));
    });
    precio.addEventListener("input", function () {
      marcar("precio");
      var p = leerNumero(precio.value);
      if (costo > 0 && p > 0) margen.value = String(Math.round((p / costo - 1) * 1000) / 10).replace(".", ",");
    });
  });

  // ------------------------------------------------------------------ tablas
  document.querySelectorAll("input[data-buscar]").forEach(function (campo) {
    var tabla = document.querySelector(campo.dataset.buscar);
    campo.addEventListener("input", function () {
      var q = norm(campo.value);
      tabla.querySelectorAll("tbody tr").forEach(function (fila) {
        fila.classList.toggle("oculto-busqueda", q !== "" && norm(fila.textContent).indexOf(q) < 0);
      });
    });
  });

  document.querySelectorAll("input[data-mostrar-inactivos]").forEach(function (casilla) {
    var tabla = document.querySelector(casilla.dataset.mostrarInactivos);
    var aplicar = function () { tabla.classList.toggle("ver-inactivos", casilla.checked); };
    casilla.addEventListener("change", aplicar);
    aplicar();
  });

  // Pedir confirmación antes de acciones importantes.
  document.querySelectorAll("form[data-confirmar]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirmar)) e.preventDefault();
    });
  });

  // Evitar doble envío (tocar dos veces "Guardar"). Las descargas (GET) no se bloquean.
  document.querySelectorAll("form[method=post]:not([data-descarga])").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      var boton = form.querySelector("button[type=submit]");
      if (boton) setTimeout(function () { boton.disabled = true; boton.textContent = "Guardando…"; }, 0);
    });
  });

  // Etiquetas: al elegir otro tamaño se guarda solo y se actualiza la vista previa.
  document.querySelectorAll("[data-enviar-al-cambiar]").forEach(function (campo) {
    campo.addEventListener("change", function () { campo.form.submit(); });
  });

  // Gráfico de costos: al pasar o tocar un punto se ve la fecha, el proveedor y el costo.
  document.querySelectorAll(".grafico-envoltura").forEach(function (caja) {
    var globo = caja.querySelector(".tooltip-grafico");
    var mostrar = function (punto) {
      var r = punto.getBoundingClientRect(), c = caja.getBoundingClientRect();
      globo.textContent = punto.getAttribute("data-info");
      globo.hidden = false;
      var x = r.left - c.left + r.width / 2;
      globo.style.left = Math.max(8, Math.min(x - globo.offsetWidth / 2, c.width - globo.offsetWidth - 8)) + "px";
      globo.style.top = Math.max(0, r.top - c.top - globo.offsetHeight - 10) + "px";
    };
    caja.querySelectorAll("circle[data-info]").forEach(function (punto) {
      ["mouseenter", "focus", "click"].forEach(function (ev) {
        punto.addEventListener(ev, function () { mostrar(punto); });
      });
      ["mouseleave", "blur"].forEach(function (ev) {
        punto.addEventListener(ev, function () { globo.hidden = true; });
      });
    });
  });

  // El stock se actualiza solo (por ejemplo cuando llega una factura por Telegram).
  var celdas = document.querySelectorAll("[data-stock-id]");
  if (celdas.length) {
    var actualizar = function () {
      fetch("/api/stock", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (datos) {
          if (!datos) return;
          celdas.forEach(function (celda) {
            var valor = datos.stock[celda.dataset.stockId] || 0;
            var texto = cant(valor);
            if (celda.textContent.trim() !== texto) {
              celda.textContent = texto;
              celda.classList.add("cambio");
              setTimeout(function () { celda.classList.remove("cambio"); }, 2500);
            }
            celda.classList.toggle("rojo", valor <= 0);
          });
        })
        .catch(function () {});
    };
    setInterval(actualizar, 10000);
  }
})();

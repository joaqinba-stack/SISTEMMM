// Ayudas de la página: cálculos automáticos, búsqueda y stock que se actualiza solo.
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

  function miles(n) {
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }
  function gs(n) { return isFinite(n) ? "₲ " + miles(n) : "—"; }
  function cant(n) {
    n = Math.round(n * 1000) / 1000;
    return Number.isInteger(n) ? miles(n) : String(n).replace(".", ",");
  }

  // Campos de dinero: al salir del campo se muestran con puntos de miles.
  document.querySelectorAll("input[data-dinero]").forEach(function (campo) {
    campo.addEventListener("blur", function () {
      var n = leerNumero(campo.value);
      if (isFinite(n)) campo.value = miles(n);
    });
    var n = leerNumero(campo.value);
    if (isFinite(n)) campo.value = miles(n);
  });

  // Precio unitario, % de ganancia y precio de venta.
  document.querySelectorAll("form[data-calculo-precio]").forEach(function (form) {
    var cantidad = form.querySelector("#cantidad");
    var costo = form.querySelector("#costo_total");
    var unitarioTxt = form.querySelector("#precio_unitario");
    var costoBase = form.querySelector("#costo_base");
    var margen = form.querySelector("#margen_pct");
    var precio = form.querySelector("#precio_venta");
    var opcional = precio && precio.hasAttribute("data-opcional");
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
      var sugerido = Math.round(u * (1 + m / 100));
      if (opcional) {
        precio.placeholder = "Sugerido: " + gs(sugerido);
      } else if (!precioEscritoAMano) {
        precio.value = miles(sugerido);
      }
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

    // Compra nueva: al elegir el producto se completa el proveedor y su % de ganancia.
    var elegir = form.querySelector("[data-elegir-producto]");
    if (elegir) elegir.addEventListener("change", function () {
      var op = elegir.selectedOptions[0];
      if (!op) return;
      var prov = form.querySelector("#proveedor");
      if (prov && !prov.value) prov.value = op.dataset.proveedor || "";
      if (margen && op.dataset.margen) margen.value = parseFloat(op.dataset.margen);
      recalcular();
    });
  });

  // Venta: al elegir el producto se pone su precio y se calcula el total.
  var elegirVenta = document.querySelector("[data-elegir-venta]");
  if (elegirVenta) {
    var cantVenta = document.getElementById("venta_cantidad");
    var precioVenta = document.getElementById("venta_precio");
    var totalVenta = document.getElementById("venta_total");
    var calcularTotal = function () {
      var t = leerNumero(cantVenta.value) * leerNumero(precioVenta.value);
      totalVenta.textContent = gs(t);
    };
    elegirVenta.addEventListener("change", function () {
      var op = elegirVenta.selectedOptions[0];
      if (op) precioVenta.value = miles(parseFloat(op.dataset.precio || "0"));
      calcularTotal();
    });
    cantVenta.addEventListener("input", calcularTotal);
    precioVenta.addEventListener("input", calcularTotal);
  }

  // Buscar en una tabla.
  document.querySelectorAll("input[data-buscar]").forEach(function (campo) {
    var tabla = document.querySelector(campo.dataset.buscar);
    campo.addEventListener("input", function () {
      var q = campo.value.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
      tabla.querySelectorAll("tbody tr").forEach(function (fila) {
        var texto = fila.textContent.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
        fila.classList.toggle("oculto-busqueda", q !== "" && texto.indexOf(q) < 0);
      });
    });
  });

  // Mostrar u ocultar productos inactivos.
  document.querySelectorAll("input[data-mostrar-inactivos]").forEach(function (casilla) {
    var tabla = document.querySelector(casilla.dataset.mostrarInactivos);
    var aplicar = function () { tabla.classList.toggle("ver-inactivos", casilla.checked); };
    casilla.addEventListener("change", aplicar);
    aplicar();
  });

  // Filtrar las opciones de una lista de productos mientras se escribe.
  document.querySelectorAll("input[data-filtrar-select]").forEach(function (campo) {
    var lista = document.querySelector(campo.dataset.filtrarSelect);
    campo.addEventListener("input", function () {
      var q = campo.value.toLowerCase();
      var primera = null;
      Array.prototype.forEach.call(lista.options, function (op) {
        var ve = op.textContent.toLowerCase().indexOf(q) >= 0;
        op.hidden = !ve;
        if (ve && !primera) primera = op;
      });
      if (primera && q) {
        lista.value = primera.value;
        lista.dispatchEvent(new Event("change"));
      }
    });
  });

  // Pedir confirmación antes de acciones importantes.
  document.querySelectorAll("form[data-confirmar]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirmar)) e.preventDefault();
    });
  });

  // Evitar doble envío (tocar dos veces "Guardar").
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      var boton = form.querySelector("button[type=submit]");
      if (boton) setTimeout(function () { boton.disabled = true; boton.textContent = "Guardando…"; }, 0);
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

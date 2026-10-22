/* Operaciones / Búsquedas (I2) — mejora progresiva de los formularios (REG-CH-01 y cancelación).
   Sin framework nuevo y SIN estilos propios (todo el aspecto es del core del Hub). El servidor valida TODO igual:
   - campos condicionales (`[data-when-field]`): se ocultan y se deshabilitan según otro campo;
   - doble envío: el segundo submit se ignora y el botón queda en «Enviando…»;
   - sin JS la página funciona igual (todo visible; el servidor descarta lo que no aplica). */
(function () {
  "use strict";

  function fieldValue(form, name) {
    var el = form.elements[name];
    return el && typeof el.value === "string" ? el.value : "";
  }

  function applyConditionals(form) {
    form.querySelectorAll("[data-when-field]").forEach(function (box) {
      var values = (box.getAttribute("data-when-values") || "").split("|");
      var show = values.indexOf(fieldValue(form, box.getAttribute("data-when-field"))) !== -1;
      box.style.display = show ? "" : "none";
      box.querySelectorAll("input, select, textarea").forEach(function (el) {
        el.disabled = !show;
        if (el.hasAttribute("data-required-when-visible")) el.required = show;
      });
    });
  }

  function lockOnSubmit(form) {
    form.addEventListener("submit", function (ev) {
      var submitter = ev.submitter;
      if (submitter && submitter.getAttribute("formmethod") === "dialog") return;   // «Volver» del modal: no envía nada
      if (form.getAttribute("data-submitting") === "1") { ev.preventDefault(); return; }
      form.setAttribute("data-submitting", "1");
      var button = form.querySelector('button[type="submit"][data-busy-label]');
      var status = form.querySelector("[data-status]");
      var label = button && button.getAttribute("data-busy-label");
      if (button && !button.hasAttribute("data-label")) button.setAttribute("data-label", button.textContent.trim());
      if (label && status) status.textContent = label;
      if (button) {
        button.setAttribute("aria-busy", "true");
        if (label) button.textContent = label;
        // Se deshabilita DESPUÉS de que el navegador arma el envío (evita perder el submit).
        window.setTimeout(function () { button.disabled = true; }, 0);
      }
    });
  }

  function init() {
    document.querySelectorAll("form[data-opsb-form]").forEach(function (form) {
      if (form.hasAttribute("data-mode")) {
        applyConditionals(form);
        form.addEventListener("change", function () { applyConditionals(form); });
      }
      lockOnSubmit(form);
    });
  }

  // Volver atrás con bfcache: el formulario no debe quedar bloqueado.
  window.addEventListener("pageshow", function (ev) {
    if (!ev.persisted) return;
    document.querySelectorAll("form[data-opsb-form][data-submitting]").forEach(function (form) {
      form.removeAttribute("data-submitting");
      var button = form.querySelector('button[type="submit"][data-busy-label]');
      if (button) {
        button.disabled = false; button.removeAttribute("aria-busy");
        if (button.hasAttribute("data-label")) button.textContent = button.getAttribute("data-label");
      }
      var status = form.querySelector("[data-status]");
      if (status) status.textContent = "";
    });
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

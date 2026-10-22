/* Operaciones / Búsquedas (I2) — mejora progresiva de los formularios (REG-CH-01 y cancelación).
   Sin framework nuevo y SIN estilos propios (todo el aspecto es del core del Hub). El servidor valida TODO igual:
   - campos condicionales (`[data-when-field]`): se ocultan y se deshabilitan según otro campo;
   - estado de carga del envío: MISMO patrón del Hub que «Cargar CV» de CCHH (botón con carga integrada `.asr-generate`): el botón que envía recibe `.htmx-request` (spinner + texto de
     carga) y se deshabilita DESPUÉS del evento (un submitter deshabilitado antes perdería su name/value). El segundo submit se ignora (la idempotencia real sigue en el servidor);
   - la confirmación de éxito NO la pinta este script: llega recién con la redirección que confirma el servidor (`?ok=`). Si el servidor responde con errores la página se re-renderiza con los
     datos preservados; si se vuelve atrás desde el historial (bfcache) `pageshow` restaura el estado interactivo;
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

  function submitButtons(form) {
    return Array.prototype.slice.call(form.querySelectorAll('button[type="submit"]'));
  }

  function lockOnSubmit(form) {
    form.addEventListener("submit", function (ev) {
      var submitter = ev.submitter;
      if (submitter && submitter.getAttribute("formmethod") === "dialog") return;   // «Volver» del modal: no envía nada
      if (form.dataset.opsbBusy === "true") { ev.preventDefault(); return; }
      var buttons = submitButtons(form);
      var active = (submitter && buttons.indexOf(submitter) !== -1) ? submitter : buttons.filter(function (b) { return b.classList.contains("asr-generate"); })[0];
      form.dataset.opsbBusy = "true";
      form.setAttribute("aria-busy", "true");
      if (active) active.classList.add("htmx-request");
      window.setTimeout(function () {
        if (form.dataset.opsbBusy !== "true") return;
        buttons.forEach(function (b) { b.disabled = true; });
      }, 0);
    });
  }

  function restore(form) {
    delete form.dataset.opsbBusy;
    form.removeAttribute("aria-busy");
    submitButtons(form).forEach(function (b) { b.disabled = false; b.classList.remove("htmx-request"); });
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

  window.addEventListener("pageshow", function (ev) {
    if (!ev.persisted) return;
    document.querySelectorAll("form[data-opsb-form]").forEach(restore);
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

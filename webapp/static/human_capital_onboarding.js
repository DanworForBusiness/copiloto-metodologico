/* Capital Humano / Onboarding — preferencia MANUAL de abrir/cerrar los contenedores del checklist (mejora progresiva; el estado inicial lo define el servidor).
   Se recuerda por onboarding (sessionStorage, solo la preferencia de la persona: ninguna credencial) y se reaplica tras cada reemplazo HTMX del checklist.
   Un único wiring global, delegado en `document`: el fragmento `#hc-onboarding-checklist` se reemplaza entero tras cada tildado y este archivo se carga una sola vez con la página.
   Sin JS (o con el storage deshabilitado) el valor por defecto del servidor sigue valiendo. */
(function () {
  "use strict";
  if (window.__hcOnbGroupsWired) return;
  window.__hcOnbGroupsWired = true;

  function wrap() { return document.getElementById("hc-onboarding-checklist"); }
  function key(app) { return "dw-onb-collapse:" + app; }
  function read(app) {
    try { return JSON.parse(sessionStorage.getItem(key(app)) || "{}") || {}; } catch (e) { return {}; }
  }
  function write(app, state) {
    try { sessionStorage.setItem(key(app), JSON.stringify(state)); } catch (e) { /* storage deshabilitado: el valor por defecto sigue valiendo */ }
  }

  function apply() {                                          // reaplica SOLO lo que la persona abrió/cerró a mano; el resto conserva el valor por defecto del servidor
    var w = wrap();
    if (!w) return;
    var state = read(w.getAttribute("data-onboarding-application"));
    w.querySelectorAll("details[data-section-id]").forEach(function (d) {
      var id = d.getAttribute("data-section-id");
      if (Object.prototype.hasOwnProperty.call(state, id)) {
        if (state[id]) d.setAttribute("open", ""); else d.removeAttribute("open");
      }
    });
  }

  document.addEventListener("click", function (ev) {          // solo el click de la persona sobre un encabezado cuenta como preferencia manual (un cambio automático por reemplazo no)
    var summary = ev.target.closest && ev.target.closest("#hc-onboarding-checklist summary");
    if (!summary) return;
    var d = summary.parentElement, w = wrap();
    if (!w) return;
    setTimeout(function () {                                  // el navegador alterna `open` DESPUÉS del click: se lee el estado ya resuelto
      var app = w.getAttribute("data-onboarding-application"), state = read(app);
      state[d.getAttribute("data-section-id")] = d.hasAttribute("open");
      write(app, state);
    }, 0);
  });
  document.body.addEventListener("htmx:afterSwap", apply);    // antes de pintar: sin parpadeo
  apply();
})();

/* hub_theme.js — contrato ÚNICO del tema de contenido del Hub (claro/oscuro).
   Antes vivía como método del componente Alpine `copilotLayout().toggleTheme()` (app.js): inutilizable fuera de una página del Hub. Se extrajo SIN cambiar el contrato: la preferencia es
   `localStorage["dw-content-theme"]` (+ cookie del mismo nombre, que leen los HTML embebidos en el servidor), el tema vive como clase `content-theme-light|dark` en <html>, y el Hub avisa a los
   documentos embebidos (`iframe.content-embed`) con `postMessage({type:"danwor-theme", theme})`. Lo consumen `toggleTheme()` (app.js) y las presentaciones abiertas en pestaña propia (R1).
   Los otros dos puntos que aplican tema siguen siendo propios por necesidad: el script síncrono del <head> de base.html (debe correr antes del primer paint) y el de /pack-assets
   (inyectado en HTML de pack). Ver docs/COMMERCIAL_PRESENTATIONS.md. */
(function () {
  "use strict";
  var KEY = "dw-content-theme";

  function norm(theme) { return theme === "dark" ? "dark" : "light"; }

  function get() {
    try { return norm(localStorage.getItem(KEY) || "light"); } catch (e) { return "light"; }
  }

  // Tema vigente de ESTE documento: el que ya trae <html> (p. ej. aplicado en el servidor desde la cookie) o, si no trae ninguno, la preferencia guardada.
  function current() {
    var root = document.documentElement.classList;
    return root.contains("content-theme-dark") ? "dark" : root.contains("content-theme-light") ? "light" : get();
  }

  // Refleja el tema en <html> y en los íconos de toggle declarativos (`[data-dw-theme-icon]`: visible solo el del tema actual; los que traen `x-show` los gobierna Alpine).
  function apply(theme) {
    theme = norm(theme);
    var root = document.documentElement.classList;
    root.remove("content-theme-light", "content-theme-dark");
    root.add("content-theme-" + theme);
    document.querySelectorAll("[data-dw-theme-icon]").forEach(function (icon) {
      if (!icon.hasAttribute("x-show")) icon.style.display = icon.getAttribute("data-dw-theme-icon") === theme ? "" : "none";   // (`hidden` no oculta SVG)
    });
    return theme;
  }

  // Cambia y PERSISTE el tema (preferencia + cookie) y avisa a los documentos embebidos.
  function set(theme) {
    theme = apply(theme);
    try { localStorage.setItem(KEY, theme); } catch (e) { /* modo privado/cuota: el tema rige igual en esta página */ }
    try { document.cookie = KEY + "=" + theme + ";path=/;max-age=31536000;samesite=lax"; } catch (e) { /* sin cookies */ }
    document.querySelectorAll("iframe.content-embed").forEach(function (frame) {
      try { frame.contentWindow.postMessage({ type: "danwor-theme", theme: theme }, "*"); } catch (e) { /* frame aún sin documento */ }
    });
    return theme;
  }

  // Para un documento que SIGUE al Hub: cambios hechos desde el Hub embebiéndolo (mensaje `danwor-theme`, mismo origen) o desde otra pestaña del Hub (evento `storage`).
  function listen() {
    window.addEventListener("message", function (event) {
      if (event.origin !== window.location.origin) return;
      var data = event.data;
      if (data && data.type === "danwor-theme") apply(data.theme);
    });
    window.addEventListener("storage", function (event) {
      if (event.key === KEY && event.newValue) apply(event.newValue);
    });
  }

  window.dwTheme = { get: get, current: current, apply: apply, set: set, listen: listen };
})();

/* human_capital_import.js — token con alcance de ESCRITURA para la importación histórica de CVs (owner).
 *
 * Las acciones de la importación (ejecutar el plan, completar/vincular una excepción) escriben en la carpeta administrada de CVs, así que necesitan el token de escritura de documentos (el MISMO que usa «Subir
 * documento»: `window.dwHumanCapitalDocumentWriteToken`, que sigue siendo la única autoridad del token). Este archivo NO obtiene ni guarda credenciales por su cuenta: difiere el request de HTMX hasta que la autoridad
 * responde y le inyecta el parámetro, igual que el hook transversal de lectura (hub_auth.js) pero para los elementos con `data-hc-write-token`. Sin popups automáticos (silencioso); la autorización interactiva solo ocurre
 * detrás del click de «Autorizar escritura en CVs».
 */
(function () {
  "use strict";
  var MARKER = "[data-hc-write-token]";
  var resolved = new WeakMap();

  function needsWrite(elt) { return !!(elt && elt.closest && elt.closest(MARKER)); }

  function writeToken(interactive) {
    if (typeof window.dwHumanCapitalDocumentWriteToken !== "function") return Promise.resolve("");
    return Promise.resolve(window.dwHumanCapitalDocumentWriteToken({ interactive: !!interactive })).catch(function () { return ""; });
  }

  document.addEventListener("htmx:confirm", function (event) {
    var elt = event.detail && event.detail.elt;
    if (!needsWrite(elt)) return;
    event.preventDefault();
    writeToken(false).then(function (token) {
      resolved.set(elt, token || "");
      try { event.detail.issueRequest(true); } catch (e) { resolved.delete(elt); }
    });
  });

  document.addEventListener("htmx:configRequest", function (event) {
    var elt = event.detail && event.detail.elt;
    if (!needsWrite(elt)) return;
    var token = resolved.get(elt);
    resolved.delete(elt);
    event.detail.parameters["graph_token"] = token || "";
  });

  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest && event.target.closest("[data-hc-authorize-write]");
    if (!button) return;
    var scope = button.parentElement || document;
    var status = scope.querySelector("[data-hc-authorize-status]") || document.querySelector("[data-hc-authorize-status]");
    if (status) status.textContent = "Solicitando autorización…";
    writeToken(true).then(function (token) {
      if (status) status.textContent = token ? "Escritura autorizada." : "No se pudo obtener la autorización: reintentá o volvé a iniciar sesión.";
    });
  });
  // «Históricos sin validación»: tras guardar un borrador el servidor avisa (HX-Trigger `cvexc-drafts`) cuántos hay; se actualiza el contador del panel sin recargar. Solo muestra un número.
  document.addEventListener("cvexc-drafts", function (event) {
    var total = event.detail && event.detail.value;
    var label = document.querySelector("[data-drafts-count]");
    if (label && typeof total === "number") label.textContent = "Con borrador guardado: " + total + ".";
  });
})();

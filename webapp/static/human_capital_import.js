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

  // Carga masiva → «Descartar» un CV de la bandeja: diálogo COMPARTIDO del Hub (`dwConfirmChoice`, app.js/base.html) con el motivo y la casilla DESMARCADA «Eliminar también el archivo original de SharePoint».
  // Sin casilla, el archivo se mueve a «descartados» (recuperable). Con casilla, una SEGUNDA confirmación explícita; recién entonces viajan `delete_original` y `confirm_delete` y se dispara el request HTMX del
  // botón (`hx-trigger="confirmed"`, mismo mecanismo que `[data-dw-confirm]`). Cancelar en cualquiera de los dos pasos no envía nada.
  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest && event.target.closest("[data-cvinbox-discard]");
    if (!button || typeof window.dwConfirmChoice !== "function" && typeof dwConfirmChoice !== "function") return;
    event.preventDefault();
    event.stopPropagation();
    var choose = window.dwConfirmChoice || dwConfirmChoice;
    var batch = button.hasAttribute("data-cvinbox-discard-batch");
    var selected = batch ? document.querySelectorAll("[data-cvinbox-select]:checked").length : 1;
    if (batch && !selected) return;
    var name = batch ? (selected + (selected === 1 ? " CV seleccionado" : " CVs seleccionados")) : (button.getAttribute("data-name") || "este CV");
    var options = [];
    try { options = JSON.parse(button.getAttribute("data-reasons") || "[]"); } catch (e) { options = []; }
    choose({
      title: batch ? "Descartar CVs seleccionados" : "Descartar CV",
      message: batch ? ("Los " + name + " no se van a incorporar al Banco. Sus archivos pasan a la carpeta «descartados», desde donde se pueden recuperar.")
                     : ("«" + name + "» no se va a incorporar al Banco. El archivo pasa a la carpeta «descartados», desde donde se puede recuperar."),
      confirmLabel: "Descartar",
      select: { label: "Motivo", options: options },
      checkbox: batch ? { label: "Eliminar también los archivos originales de SharePoint", hint: "Sin marcar, los archivos se conservan en «descartados». Marcada, se eliminan de SharePoint y no se pueden recuperar desde el Hub." }
                      : { label: "Eliminar también el archivo original de SharePoint", hint: "Sin marcar, el archivo se conserva en «descartados». Marcada, se elimina de SharePoint y no se puede recuperar desde el Hub." }
    }).then(function (first) {
      if (!first.ok) return;
      var vals = {};
      try { vals = JSON.parse(button.getAttribute("hx-vals") || "{}"); } catch (e) { vals = {}; }
      vals.reason = first.selected || "";
      delete vals.delete_original;
      delete vals.confirm_delete;
      var send = function () {
        button.setAttribute("hx-vals", JSON.stringify(vals));
        if (window.htmx && window.htmx.trigger) window.htmx.trigger(button, "confirmed");
      };
      if (!first.checked) { send(); return; }
      choose(batch ? { title: "Eliminar los archivos originales", message: "Se van a eliminar de SharePoint los archivos de los " + name + ". No se puede deshacer desde el Hub. ¿Confirmás la eliminación?", confirmLabel: "Eliminar archivos" }
                   : { title: "Eliminar el archivo original", message: "Se va a eliminar «" + name + "» de SharePoint. No se puede deshacer desde el Hub. ¿Confirmás la eliminación?", confirmLabel: "Eliminar archivo" })
        .then(function (second) {
          if (!second.ok) return;
          vals.delete_original = "true";
          vals.confirm_delete = "ELIMINAR";
          send();
        });
    });
  }, true);

  // Carga masiva → columnas redimensionables: el primitivo COMPARTIDO de app.js (`initResizableColumns` + `restorePersistedColumnWidths`), cableado en la carga y tras cada render del panel — MISMO
  // adaptador mínimo que usa Candidatos (solo CUÁNDO llamarlo, nunca una copia del algoritmo). `document` como scope: el primitivo busca el contenedor como descendiente.
  // Enlaces a la carpeta de carga masiva (subtítulo y guía, fuera del panel): su dirección REAL solo se conoce al leer la carpeta con Graph; el panel la expone en `data-inbox-url` y acá se asigna
  // (las subcarpetas `incorporados`/`descartados` cuelgan de esa misma ruta). Sin dirección, el texto queda sin enlace.
  function wireInboxFolderLinks() {
    var panel = document.querySelector("#cvinbox-panel[data-inbox-url]");
    if (!panel) return;
    var base = panel.getAttribute("data-inbox-url").replace(/\/+$/, "");
    document.querySelectorAll("[data-cvinbox-folder-link]").forEach(function (a) {
      var sub = a.getAttribute("data-sub");
      var shared = /\/:[a-z]:\//i.test(base);                                       // link de compartir: no admite subrutas, se abre la carpeta de entrada
      a.setAttribute("href", sub && !shared ? base + "/" + encodeURIComponent(sub) : base);
    });
  }
  // Resultado EXITOSO de una acción: pasa al toast transitorio de la página (los avisos con error o parciales quedan en el panel: piden lectura o una acción).
  function moveInboxResultToToast() {
    var toast = document.querySelector("[data-cvinbox-toast]");
    var result = document.querySelector("#cvinbox-panel [data-cvinbox-result][data-kind='ok']");
    if (!toast || !result) return;
    toast.classList.remove("is-dismissing");
    toast.replaceChildren(result);
  }
  function wireInboxColumns() {
    moveInboxResultToToast();
    wireInboxFolderLinks();
    var container = document.querySelector("#cvinbox-panel [data-resizable-columns-key]");
    if (!container) return;
    if (typeof restorePersistedColumnWidths === "function") restorePersistedColumnWidths(container);
    if (typeof initResizableColumns === "function") initResizableColumns(document);
  }
  // Una acción de la bandeja cuya respuesta NO llega (error del servidor, corte de red o un proxy que cierra un request largo): la operación pudo haberse completado igual en el servidor (la decisión es
  // durable). Nunca dejar al usuario sin saber qué pasó: aviso en el toast de la página y recarga del panel con el estado REAL (el mismo cargador de la página, con el token de LECTURA del hook compartido).
  function inboxRequestFailed(event) {
    var elt = event.detail && event.detail.elt;
    var target = event.detail && event.detail.target;
    var panel = document.getElementById("cvinbox-panel");
    if (!panel || !((elt && panel.contains(elt)) || target === panel)) return;
    if (elt && elt.hasAttribute && elt.hasAttribute("data-cvinbox-auto-retire")) return;      // reintento automático del retiro: silencioso, la fila conserva el motivo y el botón manual
    var toast = document.querySelector("[data-cvinbox-toast]");
    if (toast) {
      var note = document.createElement("aside");
      note.className = "callout callout--warn callout--feedback";
      note.setAttribute("role", "status");
      note.setAttribute("data-cvinbox-result", "");
      note.setAttribute("data-kind", "unconfirmed");
      note.textContent = "No recibimos la confirmación a tiempo. La operación puede haberse completado: actualizamos la bandeja con su estado real.";
      toast.classList.remove("is-dismissing");
      toast.replaceChildren(note);
    }
    var loader = document.createElement("div");
    loader.setAttribute("hx-post", "/cchh/candidatos/importar/bandeja");
    loader.setAttribute("hx-trigger", "load");
    loader.setAttribute("hx-target", "this");
    loader.setAttribute("hx-swap", "outerHTML");
    loader.setAttribute("data-dw-graph-token", "");
    loader.setAttribute("data-cvinbox-loader", "");
    loader.innerHTML = '<p class="section-analysis-loading"><span class="section-analysis-spinner"></span><span class="section-analysis-loading-copy">Actualizando la bandeja…</span></p>';
    panel.replaceWith(loader);
    if (window.htmx && window.htmx.process) window.htmx.process(loader);
  }
  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(function (name) { document.addEventListener(name, inboxRequestFailed); });
  document.addEventListener("htmx:afterSettle", function () { wireInboxColumns(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wireInboxColumns); else wireInboxColumns();
})();

/* human_capital_candidates_download.js — CCHH · Candidatos · «Descargar CVs» (Fase 2).

   Flujo: «Descargar CVs» (barra de selección) → POST de ids a `/cchh/candidatos/descargar/resumen` (solo metadata, sin Graph) → resumen y confirmación → POST a `/cchh/candidatos/descargar` con
   el token DELEGADO de Microsoft del usuario (`dwGraphToken()`, en el CUERPO: nunca en la URL, ni en el DOM, ni en consola, ni en almacenamiento) → ZIP (se entrega al navegador como descarga) o,
   si algún CV no se pudo obtener, el servidor NO entrega nada y pide una SEGUNDA confirmación explícita para el ZIP parcial.
   «Cancelar» (o cerrar el diálogo) aborta la solicitud: el servidor lo detecta como desconexión y cancela la lectura. La selección (ids, `sessionStorage`) la gestiona
   `human_capital_candidates_selection.js` (se usa `window.__cchhSelection.store`). Sin dependencias; los valores del servidor solo se escriben con `textContent`. */
(function () {
  "use strict";

  var SUMMARY_URL = "/cchh/candidatos/descargar/resumen";
  var DOWNLOAD_URL = "/cchh/candidatos/descargar";
  var MAX_CANDIDATES = 25;
  var state = { controller: null, working: false };

  function selectionIds(win) {
    var sel = win.__cchhSelection;
    return sel && sel.store ? sel.store.ids() : [];
  }

  function encodeForm(pairs) {
    return pairs.map(function (p) { return encodeURIComponent(p[0]) + "=" + encodeURIComponent(p[1]); }).join("&");
  }

  function parts(doc) { return { dialog: doc.getElementById("hc-download-dialog"), content: doc.getElementById("hc-download-content") }; }

  function open(doc) {
    var p = parts(doc);
    if (p.dialog && p.dialog.showModal && !p.dialog.open) p.dialog.showModal();
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // Mensajes dibujados en el navegador: SOLO textContent (nada del servidor entra como HTML).
  function renderMessage(doc, title, text, actions) {
    var p = parts(doc);
    if (!p.content) return;
    clear(p.content);
    var body = doc.createElement("div");
    body.setAttribute("class", "dw-confirm__body");
    var h = doc.createElement("h2");
    h.setAttribute("class", "dw-confirm__title");
    h.setAttribute("id", "hc-download-title");
    h.textContent = title;
    var msg = doc.createElement("p");
    msg.setAttribute("class", "dw-confirm__msg");
    msg.setAttribute("data-dl-message", "");
    msg.textContent = text;
    body.appendChild(h);
    body.appendChild(msg);
    var bar = doc.createElement("div");
    bar.setAttribute("class", "dw-confirm__actions");
    (actions || [{ label: "Cerrar", close: true }]).forEach(function (a) {
      var b = doc.createElement("button");
      b.setAttribute("type", a.close ? "submit" : "button");
      b.setAttribute("class", "btn-secondary");
      if (a.attr) b.setAttribute(a.attr, "");
      b.textContent = a.label;
      if (a.close) {
        var form = doc.createElement("form");
        form.setAttribute("method", "dialog");
        form.appendChild(b);
        bar.appendChild(form);
      } else {
        bar.appendChild(b);
      }
    });
    p.content.appendChild(body);
    p.content.appendChild(bar);
    open(doc);
  }

  function renderServerHtml(doc, html) {
    var p = parts(doc);
    if (!p.content) return;
    p.content.innerHTML = html;                                                                  // HTML del propio servidor (plantilla con autoescape), nunca construido con datos del navegador
    open(doc);
  }

  function sessionMessage(doc) {
    renderMessage(doc, "Tu sesión venció", "Volvé a iniciar sesión y repetí la descarga.");
  }

  function openSummary(doc, win) {
    var list = selectionIds(win);
    if (!list.length || list.length > MAX_CANDIDATES) return;
    win.fetch(SUMMARY_URL, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" },
                             body: encodeForm(list.map(function (id) { return ["profile_ids", id]; })) })
      .then(function (res) {
        if (res.redirected || res.status === 401) throw new Error("session");
        if (!res.ok) throw new Error("http");
        return res.text();
      })
      .then(function (html) { renderServerHtml(doc, html); })
      .catch(function (err) {
        if (err && err.message === "session") sessionMessage(doc);
        else renderMessage(doc, "No se pudo preparar la descarga", "No se pudo preparar el resumen. Reintentá en unos instantes.");
      });
  }

  function renderWorking(doc, cancellable) {
    var actions = cancellable ? [{ label: "Cancelar descarga", attr: "data-dl-cancel-run" }] : [];
    renderMessage(doc, "Preparando la descarga…", "Leyendo los CVs desde SharePoint y armando el ZIP. Puede tardar hasta un minuto: no cierres esta ventana.", actions);
    var p = parts(doc);
    var msg = p.content && p.content.querySelector ? p.content.querySelector("[data-dl-message]") : null;
    if (msg) msg.setAttribute("aria-busy", "true");
  }

  function filenameFrom(disposition) {
    var m = /filename="([A-Za-z0-9._-]{1,80})"/.exec(disposition || "");
    return m && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(m[1]) && m[1].indexOf("..") < 0 ? m[1] : "cvs-candidatos.zip";           // solo nombres simples: nunca rutas, ni «..», ni nombres ocultos
  }

  function megabytes(bytes) { return (bytes / 1048576).toFixed(1).replace(".", ",") + " MB"; }

  function saveBlob(doc, win, blob, filename) {
    var url = win.URL.createObjectURL(blob);
    var a = doc.createElement("a");
    a.setAttribute("href", url);
    a.setAttribute("download", filename);
    a.style.display = "none";
    doc.body.appendChild(a);
    a.click();
    doc.body.removeChild(a);
    win.setTimeout(function () { win.URL.revokeObjectURL(url); }, 30000);
  }

  function handleResponse(doc, win, res) {
    if (res.redirected || res.status === 401) { sessionMessage(doc); return null; }
    var type = (res.headers && res.headers.get("Content-Type")) || "";
    if (res.ok && type.indexOf("application/zip") >= 0) {
      var filename = filenameFrom(res.headers.get("Content-Disposition"));
      var files = res.headers.get("X-Export-Files");
      var partial = res.headers.get("X-Export-Status") === "partial";
      return res.blob().then(function (blob) {
        saveBlob(doc, win, blob, filename);
        renderMessage(doc, partial ? "Descarga parcial lista" : "Descarga lista",
                      (files ? files + " archivo" + (files === "1" ? "" : "s") + " · " : "") + megabytes(blob.size) + ". El ZIP se está guardando en tu equipo" +
                      (partial ? " (incluye un LEEME con los documentos omitidos)." : "."));
      });
    }
    if (type.indexOf("text/html") >= 0) return res.text().then(function (html) { renderServerHtml(doc, html); });
    renderMessage(doc, "No se pudo completar la descarga", "No se entregó ningún archivo. Reintentá en unos instantes.");
    return null;
  }

  function handleError(doc, err) {
    if (err && err.name === "AbortError") renderMessage(doc, "Descarga cancelada", "Cancelaste la descarga: no se entregó ningún archivo.");
    else renderMessage(doc, "No se pudo completar la descarga", "Se interrumpió la conexión y no se entregó ningún archivo. Reintentá en unos instantes.");
  }

  // opts: { partial: bool, ackFailed: [ids], parentOp: id }
  function startExport(doc, win, opts) {
    var p = parts(doc);
    var root = p.content && p.content.querySelector ? p.content.querySelector("[data-dl-root]") : null;
    var digest = root ? root.getAttribute("data-plan-digest") : "";
    var list = selectionIds(win);
    if (!root || !digest || !list.length || list.length > MAX_CANDIDATES || state.working) return;
    var controller = typeof win.AbortController === "function" ? new win.AbortController() : null;
    state.controller = controller;
    state.working = true;
    renderWorking(doc, !!controller);
    var getToken = typeof win.dwGraphToken === "function" ? win.dwGraphToken : function () { return ""; };
    Promise.resolve().then(function () { return getToken(); }).then(function (token) {
      if (!token) {
        renderMessage(doc, "Falta el permiso de Microsoft", "No se pudo obtener tu permiso de Microsoft (la sesión pudo vencer). Volvé a iniciar sesión y repetí la descarga.");
        return null;
      }
      var pairs = list.map(function (id) { return ["profile_ids", id]; });
      pairs.push(["graph_token", token], ["plan_digest", digest], ["ack_omitted", "1"]);                       // confirmar el resumen incluye aceptar lo que el resumen declaró omitido
      if (opts && opts.partial) {
        pairs.push(["allow_partial", "1"]);
        (opts.ackFailed || []).forEach(function (id) { pairs.push(["ack_failed", id]); });
        if (opts.parentOp) pairs.push(["parent_operation_id", opts.parentOp]);
      }
      var init = { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: encodeForm(pairs) };
      if (controller) init.signal = controller.signal;
      token = null;
      pairs = null;
      return win.fetch(DOWNLOAD_URL, init).then(function (res) { return handleResponse(doc, win, res); });
    }).catch(function (err) { handleError(doc, err); }).then(function () { state.working = false; state.controller = null; });
  }

  function cancelRun() {
    if (state.working && state.controller) state.controller.abort();
  }

  function wire(doc, win) {
    doc.addEventListener("click", function (event) {
      var t = event.target;
      var el = t && t.closest ? t.closest("[data-hc-download-open], [data-dl-confirm], [data-dl-partial], [data-dl-cancel-run], [data-dl-reopen]") : null;
      if (!el) return;
      if (el.hasAttribute("data-hc-download-open") || el.hasAttribute("data-dl-reopen")) { openSummary(doc, win); return; }
      if (el.hasAttribute("data-dl-confirm")) { startExport(doc, win, { partial: false }); return; }
      if (el.hasAttribute("data-dl-partial")) {
        var ack = (el.getAttribute("data-ack-failed") || "").split(/\s+/).filter(function (id) { return /^[A-Za-z0-9_-]{1,64}$/.test(id); });
        startExport(doc, win, { partial: true, ackFailed: ack, parentOp: /^[0-9a-f]{32}$/.test(el.getAttribute("data-parent-op") || "") ? el.getAttribute("data-parent-op") : "" });
        return;
      }
      if (el.hasAttribute("data-dl-cancel-run")) cancelRun();
    });
    var p = parts(doc);
    if (p.dialog && p.dialog.addEventListener) p.dialog.addEventListener("close", cancelRun);          // cerrar con Esc mientras se prepara = cancelar
  }

  var api = { SUMMARY_URL: SUMMARY_URL, DOWNLOAD_URL: DOWNLOAD_URL, MAX_CANDIDATES: MAX_CANDIDATES, state: state, encodeForm: encodeForm, filenameFrom: filenameFrom, openSummary: openSummary,
              startExport: startExport, cancelRun: cancelRun, renderMessage: renderMessage, wire: wire };
  if (typeof window !== "undefined") {
    window.__cchhDownload = api;
    if (typeof document !== "undefined" && document.body && !window.__cchhDownloadWired) {
      window.__cchhDownloadWired = true;
      wire(document, window);
    }
  }
})();

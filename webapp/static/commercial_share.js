/* commercial_share.js — Gestión Comercial → «Compartir»: diálogo ÚNICO (`partials/commercial_share_dialog.html`) para 02. Estrategia, una
   presentación y las presentaciones seleccionadas en 03. Presentaciones, + selección múltiple del catálogo.

   Disparadores `[data-cshare-open]`:
     · `data-cshare-kind="strategy"` / `"presentations"` con `data-cshare-items='[{"id","title","path"}]'` (Estrategia, detalle de presentación);
     · `data-cshare-open="selection"`: las presentaciones seleccionadas del catálogo (título y ruta leídos de las filas renderizadas por el servidor).
   «Copiar enlace»: la MISMA URL de siempre (`location.origin + ruta`, `copyTextToClipboard` de app.js) y el mismo aviso; con varias, «Título» + URL
   por bloque. «Enviar»: correo institucional por `POST data-send-url` con `kind` + ids (el servidor resuelve los enlaces y valida destinatarios
   del contexto Comercial). Destinatarios: módulo transversal `share_recipients.js` (mismo selector y marcado `data-share-*` que CCHH) con un host
   propio. Selección: primitivas compartidas `table_selection.js` + barra `selection_bar` (mismo patrón que Operaciones → Solicitudes). */
(function () {
  "use strict";
  var SEARCH_DEBOUNCE = 250;
  var doc = document, win = window;
  var state = null;

  function dialog() { return doc.getElementById("cshare-dialog"); }
  function field(d, attr) { var dlg = dialog(); return dlg ? dlg.querySelector("[" + attr + "]") : null; }
  function SR() { return win.dwShareRecipients; }

  // ── aviso transitorio de la página (MISMO markup `.callout` de Estrategia/Presentaciones; el host tiene `data-dw-auto-dismiss`) ──
  function toast(title, text, tone) {
    var host = doc.querySelector("[data-cp-status]") || doc.querySelector("[data-cs-status]");
    if (!host) return;
    var box = doc.createElement("div");
    box.className = "callout callout--feedback callout--" + tone;
    box.setAttribute("role", "status");
    var head = doc.createElement("div"); head.className = "callout__head";
    var icon = doc.createElement("span"); icon.className = "callout__icon"; icon.textContent = tone === "punch" ? "⚠️" : "✓";
    var heading = doc.createElement("span"); heading.className = "callout__title"; heading.textContent = title;
    head.appendChild(icon); head.appendChild(heading); box.appendChild(head);
    if (text) {
      var body = doc.createElement("div"); body.className = "callout__body";
      var p = doc.createElement("p"); p.textContent = text; body.appendChild(p); box.appendChild(body);
    }
    host.classList.remove("is-dismissing");
    host.replaceChildren(box);
  }

  function status(d, text, warn) {
    var s = field(d, "data-cshare-status");
    if (!s) return;
    s.textContent = text || ""; s.hidden = !text;
    if (warn) s.classList.add("field-hint--warn"); else s.classList.remove("field-hint--warn");
  }

  function newOperationId() {
    try { if (win.crypto && win.crypto.randomUUID) return win.crypto.randomUUID().replace(/-/g, ""); } catch (e) { /* fallback */ }
    return "op" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }

  function sendReady() {
    return !!state && state.sendEnabled && state.directoryEnabled && !state.sending && !state.sent && state.recipients.length > 0 && state.items.length > 0;
  }
  function refresh(d) {
    var btn = field(d, "data-cshare-send");
    if (!btn || !state) return;
    btn.disabled = !sendReady();
    btn.textContent = state.sending ? "Enviando…" : "Enviar";
    if (state.sending) btn.setAttribute("aria-busy", "true"); else btn.removeAttribute("aria-busy");
  }

  var HOST = { state: function () { return state; }, field: field, refresh: refresh, status: status,
               urls: { catalog: "", directory: "" }, introAttr: "data-cshare-message" };

  function texts(kind, items) {
    if (kind === "strategy") return { title: "Compartir Estrategia comercial", subject: "Estrategia comercial",
                                       message: "Hola,\n\nTe comparto la Estrategia comercial de Danwor en el Hub." };
    if (items.length === 1) return { title: "Compartir presentación", subject: "Presentación: " + items[0].title,
                                     message: "Hola,\n\nTe comparto la presentación «" + items[0].title + "»." };
    return { title: "Compartir " + items.length + " presentaciones", subject: "Presentaciones comerciales (" + items.length + ")",
             message: "Hola,\n\nTe comparto estas presentaciones comerciales." };
  }

  function open(kind, items) {
    var dlg = dialog();
    if (!dlg || !items.length) return;
    var maxR = parseInt(dlg.getAttribute("data-max-recipients"), 10);
    HOST.urls = { catalog: dlg.getAttribute("data-catalog-url"), directory: dlg.getAttribute("data-directory-url") };
    state = { kind: kind, items: items, recipients: [], directoryEnabled: !!dlg.getAttribute("data-directory-enabled"),
              provider: dlg.getAttribute("data-recipient-provider") === "allowlist" ? "allowlist" : "directory", catalog: null, greeting: "Hola,",
              sendEnabled: dlg.getAttribute("data-send-enabled") === "1", maxRecipients: isNaN(maxR) ? 20 : maxR,
              operationId: newOperationId(), sending: false, sent: false, _searchSeq: 0 };
    var t = texts(kind, items);
    field(doc, "data-cshare-title").textContent = t.title;
    field(doc, "data-cshare-subject").value = t.subject;
    field(doc, "data-cshare-message").value = t.message;
    var list = field(doc, "data-cshare-items");
    list.textContent = "";
    items.forEach(function (it) { var li = doc.createElement("li"); li.textContent = it.title; list.appendChild(li); });
    var copyBtn = field(doc, "data-cshare-copy");
    copyBtn.textContent = items.length > 1 ? "Copiar enlaces" : "Copiar enlace";
    var search = field(doc, "data-share-search");
    search.value = ""; search.disabled = !state.directoryEnabled;
    SR().renderResults(HOST, doc, []); SR().showResults(HOST, doc, false);
    SR().renderChips(HOST, doc);
    status(doc, "", false);
    refresh(doc);
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    if (state.provider === "allowlist" && state.directoryEnabled) SR().loadCatalog(HOST, doc, win);
  }

  function close() { var dlg = dialog(); if (dlg && dlg.open) dlg.close(); }

  // «Copiar enlace»: idéntico a antes para un único enlace (URL completa); con varios, bloques «Título» + URL separados por una línea en blanco.
  function copyLinks() {
    if (!state) return;
    var urls = state.items.map(function (it) { return win.location.origin + it.path; });
    var text = state.items.length === 1 ? urls[0] : state.items.map(function (it, i) { return it.title + "\n" + urls[i]; }).join("\n\n");
    var many = state.items.length > 1;
    var copier = typeof win.copyTextToClipboard === "function" ? win.copyTextToClipboard
      : function (value) { return win.navigator.clipboard.writeText(value).then(function () { return true; }); };
    Promise.resolve().then(function () { return copier(text); }).then(function (ok) {
      if (!ok) throw new Error("copy-failed");
      close();
      toast(many ? "Enlaces copiados" : "Enlace copiado", "Quien lo reciba lo abre con su propia sesión del Hub.", "success");
    }).catch(function () { status(doc, "No se pudo copiar. Copiá la dirección desde la barra del navegador.", true); });
  }

  function doSend() {
    if (!sendReady()) return;
    state.sending = true; refresh(doc); status(doc, "", false);
    var opened = state, dlg = dialog();
    SR().directoryToken(HOST, win).then(function (token) {
      if (state !== opened) return;
      if (!token && SR().needsToken(HOST)) { state.sending = false; status(doc, "No se pudo obtener autorización de Microsoft para validar los destinatarios.", true); refresh(doc); return; }
      var parts = ["operation_id=" + encodeURIComponent(state.operationId), "kind=" + encodeURIComponent(state.kind),
                   "subject=" + encodeURIComponent(field(doc, "data-cshare-subject").value),
                   "message=" + encodeURIComponent(field(doc, "data-cshare-message").value), "graph_token=" + encodeURIComponent(token || "")];
      if (state.kind === "presentations") state.items.forEach(function (it) { parts.push("presentation_ids=" + encodeURIComponent(it.id)); });
      state.recipients.forEach(function (r) { parts.push("recipients=" + encodeURIComponent(r.email)); });
      return win.fetch(dlg.getAttribute("data-send-url"), { method: "POST", credentials: "same-origin",
                                                              headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: parts.join("&") })
        .then(function (res) { return res.json().then(function (data) { return { res: res, data: data }; }, function () { return { res: res, data: {} }; }); })
        .then(function (r) { if (state === opened) applyOutcome(r.data || {}); });
    }).catch(function () {
      if (state !== opened) return;
      state.sending = false; state.sent = true;                                                    // sin respuesta: resultado incierto, no se reenvía a ciegas
      status(doc, "No se pudo confirmar el envío (conexión interrumpida). No se reenvía automáticamente: verificá antes de reintentar.", true); refresh(doc);
    });
  }

  function applyOutcome(data) {
    state.sending = false;
    var st = data.status;
    if (st === "accepted") {
      var n = (data.recipients || state.recipients).length;
      close();
      toast("Correo aceptado para envío", "Se envió a " + n + " destinatario" + (n === 1 ? "" : "s") + " desde el correo institucional.", "success");
      return;
    }
    if (st === "disabled") { state.sendEnabled = false; status(doc, data.message || "El envío por correo institucional está deshabilitado.", true); }
    else if (st === "duplicate") { state.sent = true; status(doc, "Este envío ya se había registrado; no se reenvió.", true); }
    else if (st === "uncertain") { state.sent = true; status(doc, "El envío quedó sin confirmación de Microsoft. No se reenvía automáticamente: verificá tu bandeja antes de reintentar.", true); }
    else if (st === "config_incomplete" || data.reason === "audit_unavailable" || st === "error") { status(doc, data.message || "No se pudo enviar. Revisá los destinatarios e intentá de nuevo.", true); }
    else { state.sent = true; status(doc, data.message || "El servicio de correo rechazó el envío.", true); }
    refresh(doc);
  }

  // ── Selección múltiple en 03. Presentaciones (IDs en sessionStorage; coherente con el filtro de búsqueda) ──
  var store = null;
  function bar() { return doc.querySelector("[data-cp-selection-bar]"); }
  function table() { return doc.getElementById("cp-catalog-table"); }
  function boxes() { var t = table(); return t ? [].slice.call(t.querySelectorAll("[data-cp-select]")) : []; }
  function idOf(box) { return box.getAttribute("data-cp-select"); }
  // Filas que cuenta el encabezado y «seleccionar todo»: las VISIBLES con el filtro de búsqueda (fila sin `hidden`) y habilitadas.
  function visibleBoxes() { return boxes().filter(function (b) { var tr = b.closest ? b.closest("tr") : null; return !b.disabled && !(tr && tr.hidden); }); }
  function syncSelection() {
    var b = bar(), t = table();
    if (!b || !t || !store) return;
    store.reconcile(boxes().map(idOf));                                                            // una presentación eliminada sale de la selección
    var TS = win.dwTableSelection;
    TS.syncRows(t, store, { row: "[data-cp-select]", page: "[data-cp-none]", attribute: "data-cp-select" });          // estado de cada fila (visibles y filtradas)
    var shown = visibleBoxes(), head = t.querySelector("[data-cp-select-page]");
    var view = { querySelectorAll: function (sel) { return sel === "row" ? shown : (sel === "page" && head ? [head] : []); } };
    var visible = TS.syncRows(view, store, { row: "row", page: "page", attribute: "data-cp-select" });                // encabezado: SOLO lo visible (lleno / parcial / vacío)
    var size = store.size();
    b.hidden = size === 0;
    if (size === 0) { var r = doc.getElementById("cp-catalog-panel") && doc.getElementById("cp-catalog-panel").querySelector("[data-cp-scroll-reserve]"); if (r) r.style.height = "0px"; }
    b.querySelector("[data-cp-selection-count]").textContent = size;
    b.querySelector("[data-cp-selection-label]").textContent = size === 1 ? "presentación seleccionada" : "presentaciones seleccionadas";
    var outside = size - visible.visibleSelected;
    b.querySelector("[data-cp-selection-hint]").textContent = outside > 0 ? outside + " fuera de esta búsqueda" : "";
    var share = b.querySelector("[data-cshare-open]");
    if (share) share.disabled = size === 0;
  }
  function selectedItems() {
    if (!store) return [];
    var byId = {};
    boxes().forEach(function (b) { byId[idOf(b)] = b; });
    return store.ids().map(function (id) {
      var box = byId[id];
      return box ? { id: id, title: box.getAttribute("data-cshare-title"), path: box.getAttribute("data-cshare-path") } : null;
    }).filter(Boolean);
  }
  function initSelection() {
    if (!bar() || !win.dwTableSelection) return;
    var storage = null;
    try { storage = win.sessionStorage; } catch (e) { storage = null; }
    store = win.dwTableSelection.createStore(storage, { key: "comercial-presentaciones-selection", max: 500 });
    syncSelection();
  }

  // Reserva de recorrido: con pocas filas la página entra entera en `#content` (scrollHeight = clientHeight) y `keepInPlace` no tiene hacia
  // dónde compensar la barra que aparece, así que la tabla «bajaba». Si tras compensar la fila igual se movió, se agrega al final del panel
  // EXACTAMENTE lo que falta y se desplaza: el título del agrupador sube y la fila queda quieta (misma experiencia que CCHH / Solicitudes).
  // Sin selección la reserva vuelve a 0 y el navegador ajusta el scroll solo.
  function reserve() {
    var panel = doc.getElementById("cp-catalog-panel");
    if (!panel) return null;
    var r = panel.querySelector("[data-cp-scroll-reserve]");
    if (!r) { r = doc.createElement("div"); r.setAttribute("data-cp-scroll-reserve", ""); r.setAttribute("aria-hidden", "true"); panel.appendChild(r); }
    return r;
  }
  function keepRowInPlace(anchor, change) {
    var measurable = anchor && anchor.getBoundingClientRect;
    var before = measurable ? anchor.getBoundingClientRect().top : 0;
    win.dwTableSelection.keepInPlace(doc, anchor, change);
    if (!measurable || !anchor.isConnected) return;
    var drift = anchor.getBoundingClientRect().top - before, sc = doc.getElementById("content"), r = drift > 0 ? reserve() : null;
    if (!sc || !r) return;
    r.style.height = ((parseFloat(r.style.height) || 0) + drift) + "px";
    sc.scrollTop += drift;
  }
  // Fila → esa presentación; encabezado → SOLO las visibles (la búsqueda nunca selecciona filas ocultas).
  function toggle(box) {
    if (!store || !box) return;
    var ids = box.hasAttribute("data-cp-select") ? [idOf(box)] : visibleBoxes().map(idOf);
    keepRowInPlace(box.closest ? box.closest("tr") : null, function () { if (box.checked) store.add(ids); else store.remove(ids); syncSelection(); });
  }
  function clearSelection() { if (store) { store.clear(); syncSelection(); } }
  doc.addEventListener("change", function (ev) {
    var box = ev.target;
    if (box && box.matches && box.matches("[data-cp-select], [data-cp-select-page]")) toggle(box);
  });
  doc.addEventListener("cp:rows-changed", function () { syncSelection(); });
  doc.addEventListener("input", function (ev) {
    var t = ev.target;
    if (t && t.matches && t.matches("[data-cp-search-input]")) { syncSelection(); return; }        // el filtro ya ocultó filas (listener del input corre antes)
    if (t && t.matches && t.matches("#cshare-dialog [data-share-search]")) {
      if (!state || !state.directoryEnabled) return;
      if (searchTimer) win.clearTimeout(searchTimer);
      searchTimer = win.setTimeout(function () { SR().runSearch(HOST, doc, win); }, SEARCH_DEBOUNCE);
    }
  });
  var searchTimer = null;
  doc.addEventListener("focusin", function (ev) {
    var t = ev.target;
    if (state && state.directoryEnabled && t && t.matches && t.matches("#cshare-dialog [data-share-search]") && (t.value || "").trim().length >= SR().MIN_QUERY) SR().runSearch(HOST, doc, win);
  });
  doc.addEventListener("click", function (ev) {
    var t = ev.target;
    var el = t && t.closest ? t.closest("[data-cshare-open], [data-cp-clear-selection], #cshare-dialog [data-share-pick], #cshare-dialog [data-share-chip-remove], #cshare-dialog [data-cshare-copy], #cshare-dialog [data-cshare-send]") : null;
    if (!el) {
      var results = field(doc, "data-share-results");
      if (results && !results.hidden && !(t && t.closest && t.closest("[data-share-recipients]"))) SR().showResults(HOST, doc, false);
      return;
    }
    if (el.hasAttribute("data-cshare-open")) {
      if (el.getAttribute("data-cshare-open") === "selection") { open("presentations", selectedItems()); return; }
      var items = [];
      try { items = JSON.parse(el.getAttribute("data-cshare-items") || "[]"); } catch (e) { items = []; }
      open(el.getAttribute("data-cshare-kind") === "strategy" ? "strategy" : "presentations", items);
      return;
    }
    if (el.hasAttribute("data-cp-clear-selection")) { clearSelection(); return; }
    if (el.hasAttribute("data-share-pick")) {
      SR().addRecipient(HOST, doc, { email: el.getAttribute("data-share-pick"), name: el.getAttribute("data-share-pick-name") });
      var search = field(doc, "data-share-search");
      if (search) { search.value = ""; search.focus(); }
      SR().renderResults(HOST, doc, []); SR().showResults(HOST, doc, false);
      return;
    }
    if (el.hasAttribute("data-share-chip-remove")) { SR().removeRecipient(HOST, doc, el.getAttribute("data-share-chip-remove")); return; }
    if (el.hasAttribute("data-cshare-copy")) { copyLinks(); return; }
    if (el.hasAttribute("data-cshare-send")) { doSend(); return; }
  });

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", initSelection); else initSelection();
  win.dwCommercialShare = { open: open, copyLinks: copyLinks, send: doSend, selectedItems: selectedItems, syncSelection: syncSelection, toggle: toggle,
                            clearSelection: clearSelection, initSelection: initSelection, addRecipient: function (p) { SR().addRecipient(HOST, doc, p); }, state: function () { return state; } };      // costura de pruebas
})();

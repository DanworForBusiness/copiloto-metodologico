/* operations_timenotes.js — Operaciones · OP01 · vista «Carga de Horas».
   Homologado con Capital Humano → Candidatos (filtros `.hc-profile-filters`, barra contextual `selection_bar`, tabla `.dashboard-table`) y con
   el encabezado contextual del módulo. SIN componentes/CSS nuevos. El token Graph delegado se pide con `window.dwGraphToken()`; la confirmación
   usa el modal compartido `window.dwConfirm`. Delega en document (sobrevive a los swaps de HTMX). No toca cálculo, envío ni padrón. */
(function () {
  "use strict";

  var PREVIEW_URL = "/operaciones/staffing/carga-horas/preview";
  var NOTIFY_URL = "/operaciones/staffing/carga-horas/notificar";
  var EXC_ADD_URL = "/operaciones/staffing/carga-horas/excepciones/mover";
  var EXC_REMOVE_URL = "/operaciones/staffing/carga-horas/excepciones/devolver";
  var LOADING = '<p class="section-analysis-loading"><span class="section-analysis-spinner"></span><span class="section-analysis-loading-copy">Cargando…</span></p>';
  var CARGA = { title: "Carga de Horas", desc: "Seguimiento operativo de incumplimientos de carga horaria." };
  var origHero = null;      // {title, desc} de Asignaciones, capturado una vez
  var currentView = "asignaciones";

  function root(doc) { return doc.querySelector("[data-timenotes-root]"); }
  function qsa(el, sel) { return el ? [].slice.call(el.querySelectorAll(sel)) : []; }
  function tbody(doc, which) { var r = root(doc); return r && r.querySelector('[data-tn-tbody="' + which + '"]'); }
  function colRows(doc) { return qsa(tbody(doc, "colaboradores"), "tr[data-tn-email]"); }
  function plural(n) { return n + " colaborador" + (n === 1 ? "" : "es"); }

  // ───────────── Estado de asignación (P7): define el UNIVERSO (KPIs + tabla); búsqueda y Cumplimiento solo refinan la tabla ─────────────
  function estadosSel(r) { return qsa(r, "[data-tn-estado-opt]:checked").map(function (c) { return c.value; }); }
  // Universo = Estado ∩ Disponibilidad de asignación (los dos definen KPIs, contadores y selección; búsqueda y Cumplimiento solo refinan).
  function dispSel(r) { var d = r.querySelector("[data-tn-filter-disp]"); return d ? d.value : ""; }
  function inUniverse(r, row, sel) {
    var disp = dispSel(r);
    if (disp && (row.getAttribute("data-tn-disp") || "Sin dato") !== disp) return false;
    if (r.getAttribute("data-estados-ok") !== "1") return true;                 // estados no disponibles: sin filtro (y el envío queda bloqueado)
    var es = (row.getAttribute("data-tn-estados") || "").split("|");
    return sel.some(function (s) { return es.indexOf(s) >= 0; });
  }
  function universeRows(doc) { var r = root(doc), sel = estadosSel(r); return colRows(doc).filter(function (row) { return inUniverse(r, row, sel); }); }
  function needsInactivos(sel) { return sel.indexOf("Inactivo") >= 0 || sel.indexOf("Sin estado") >= 0; }

  // ───────────── encabezado contextual (hero) ─────────────
  function hero(doc) { return { title: doc.querySelector("[data-opsb-hero-title]"), desc: doc.querySelector("[data-opsb-hero-desc]"), meta: doc.querySelector("[data-opsb-hero-meta]") }; }
  function captureHero(doc) { if (origHero) return; var h = hero(doc); if (h.title && h.desc) origHero = { title: h.title.textContent, desc: h.desc.textContent }; }
  function setHero(doc, view) {
    captureHero(doc);
    var h = hero(doc); if (!h.title || !h.desc) return;
    if (view === "carga-horas") { h.title.textContent = CARGA.title; h.desc.textContent = CARGA.desc; }       // los metadatos se llenan tras el swap
    else { if (origHero) { h.title.textContent = origHero.title; h.desc.textContent = origHero.desc; } hideHeroMeta(doc); }
  }
  function hideHeroMeta(doc) { var h = hero(doc); if (h.meta) { h.meta.hidden = true; h.meta.textContent = ""; } }
  function fillHeroMeta(doc) {
    var h = hero(doc), r = root(doc);
    if (!h.meta) return;
    if (!r) { hideHeroMeta(doc); return; }                                                                    // panel de error: sin metadatos
    var span = doc.createElement("span");
    span.appendChild(doc.createTextNode("Semana consultada " + (r.getAttribute("data-semana-label") || "—") + " · Fuente actualizada "));
    // Fecha de la fuente como enlace SOLO con el `webUrl` real que devolvió Graph (nunca una URL armada a mano).
    var url = r.getAttribute("data-fuente-url") || "", fecha = r.getAttribute("data-fuente") || "—";
    if (/^https:\/\//.test(url)) { var a = doc.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener"; a.textContent = fecha; a.title = "Abrir Time Logs Reports.csv en SharePoint"; span.appendChild(a); }
    else span.appendChild(doc.createTextNode(fecha));
    var n = parseInt(r.getAttribute("data-feriados-n") || "0", 10), fer;
    if (r.getAttribute("data-feriados-ok") !== "1") fer = "Feriados sin verificar";
    else fer = n ? "Feriados " + n + " (−" + (r.getAttribute("data-feriados-desc") || "0") + " h)" : "Sin feriados";
    span.appendChild(doc.createTextNode(" · Horas base " + (r.getAttribute("data-horas-base") || "—") + " h · "));
    var fs = doc.createElement("span"); fs.textContent = fer;
    var det = r.getAttribute("data-feriados-detalle") || ""; if (det) fs.title = det;
    span.appendChild(fs);
    if (r.getAttribute("data-simulacion") === "1") span.appendChild(doc.createTextNode(" · Simulación"));       // discreto: el aviso explica el motivo
    h.meta.textContent = ""; h.meta.appendChild(span); h.meta.hidden = false;
  }

  // ───────────── botonera de vista (estado activo + transición inmediata; HTMX hace la carga) ─────────────
  function toggleView(doc, btn) {
    var group = doc.getElementById("opsb-viewswitch");
    if (!group || btn.classList.contains("is-active")) return;
    qsa(group, "button").forEach(function (b) { b.classList.remove("is-active"); b.setAttribute("aria-pressed", "false"); });
    btn.classList.add("is-active"); btn.setAttribute("aria-pressed", "true");
    currentView = btn.getAttribute("data-opsb-view") || "asignaciones";
    setHero(doc, currentView);                                   // título/descripción cambian YA; metadatos tras el swap
    var rows = doc.getElementById("staffing-rows");
    // Cancela (mecanismo nativo `htmx:abort`) lo que siga en vuelo hacia el contenedor desde la vista anterior: carga inicial de Asignaciones
    // (#staffing-rows), la otra pestaña de la botonera y los controles de Carga de Horas. Lo que no se pueda cancelar lo descarta
    // `viewGuard` (abajo) antes de tocar el DOM.
    if (window.htmx && rows) {
      [rows].concat(qsa(group, "button"), qsa(rows, "[hx-post]")).forEach(function (el) { if (el !== btn) window.htmx.trigger(el, "htmx:abort"); });
    }
    if (rows) rows.innerHTML = LOADING;                          // retira el contenido anterior y muestra el preloader del Hub
  }

  // ───────────── filtros en cliente (Buscar / Cumplimiento) ─────────────
  function applyFilters(doc) {
    var r = root(doc); if (!r) return;
    var sEl = r.querySelector("[data-tn-search]"), eEl = r.querySelector("[data-tn-filter-estado]");
    var q = (sEl && sEl.value || "").trim().toLowerCase();
    var estado = (eEl && eEl.value) || "";
    var rows = colRows(doc), shown = 0, sel = estadosSel(r), universe = 0;
    rows.forEach(function (row) {
      var inU = inUniverse(r, row, sel);
      if (inU) universe++;
      var name = (row.getAttribute("data-tn-nombre") || "").toLowerCase();
      var email = (row.getAttribute("data-tn-email") || "").toLowerCase();
      var est = " " + (row.getAttribute("data-tn-estado") || "") + " ";
      var show = inU && (!q || name.indexOf(q) >= 0 || email.indexOf(q) >= 0) && (!estado || est.indexOf(" " + estado + " ") >= 0);
      row.hidden = !show;
      if (show) shown++;
    });
    // Contador del encabezado: visibles vs universo (MISMO `.dashboard-section__count`); sin filtros = universo solo. Estado vacío por filtros.
    var count = r.querySelector('[data-tn-count="colaboradores"]');
    if (count) count.textContent = (q || estado) ? shown + " de " + plural(universe) : plural(universe);
    var wrap = r.querySelector("[data-tn-table-wrap]"), empty = r.querySelector("[data-tn-empty]");
    if (wrap) wrap.hidden = shown === 0;
    if (empty) empty.hidden = shown !== 0;
    refreshKpis(doc);
    syncSelection(doc);
  }

  // ───────────── KPIs globales (universo Colaboradores; independientes de los filtros de la tabla) ─────────────
  function refreshKpis(doc) {
    var r = root(doc); if (!r) return;
    var rows = universeRows(doc);
    function n(attr) { return rows.filter(function (tr) { return tr.getAttribute(attr) === "1"; }).length; }
    var k = { esperados: rows.length, cumplen: n("data-tn-cumple"), con_faltantes: n("data-tn-faltantes") };
    Object.keys(k).forEach(function (key) { var el = r.querySelector('[data-tn-kpi="' + key + '"]'); if (el) el.textContent = k[key]; });
    var kc = r.querySelector("[data-tn-kpi-count]"); if (kc) kc.textContent = plural(rows.length);
    var exRows = qsa(tbody(doc, "excepciones"), "tr[data-tn-email]").length;
    var ec = r.querySelector('[data-tn-count="excepciones"]'); if (ec) ec.textContent = exRows + (exRows === 1 ? " excepción" : " excepciones");
    var ew = r.querySelector("[data-tn-exc-wrap]"), ee = r.querySelector("[data-tn-exc-empty]");
    if (ew) ew.hidden = exRows === 0;
    if (ee) ee.hidden = exRows !== 0;
  }

  // ───────────── selección (barra contextual del macro `selection_bar`) ─────────────
  // ───────────── selección múltiple: ADAPTADOR de las primitivas transversales `window.dwTableSelection` (table_selection.js) ─────────────
  // MISMO patrón que Presentaciones (commercial_share.js): store de ids + `syncRows` para filas/is-selected + encabezado SOLO sobre lo visible
  // (vista acotada) + `keepInPlace` al aparecer/desaparecer la barra. Reglas propias de Carga de Horas por las interfaces del store:
  //   · ids = emails (`idPattern`); sin storage (la selección vive en la vista cargada: una semana nueva arranca vacía);
  //   · universo seleccionable = filas elegibles (`[data-tn-select]`) de Colaboradores dentro del Estado elegido: `reconcile` saca lo que
  //     salió de ese universo (Estado, Excepciones), nunca solo por búsqueda/Cumplimiento (eso queda «fuera de este filtro», como la referencia).
  var store = null;
  function newStore(win) { store = win.dwTableSelection ? win.dwTableSelection.createStore(null, { key: "ops-timenotes-selection", idPattern: /^[^\s@]{1,64}@[^\s@]{1,190}$/, max: 2000 }) : null; }
  function boxes(doc) { return qsa(tbody(doc, "colaboradores"), "[data-tn-select]"); }
  function idOf(box) { return box.getAttribute("data-tn-select"); }
  function visibleBoxes(doc) { return boxes(doc).filter(function (b) { var tr = b.closest ? b.closest("tr") : null; return !b.disabled && !(tr && tr.hidden); }); }
  function selected() { return store ? store.ids() : []; }
  function syncSelection(doc) {
    var r = root(doc), win = doc.defaultView || window, TS = win.dwTableSelection;
    if (!r || !TS) return;
    if (!store) newStore(win);
    var sel = estadosSel(r);
    store.reconcile(boxes(doc).filter(function (b) { return inUniverse(r, b.closest("tr"), sel); }).map(idOf));
    var tb = tbody(doc, "colaboradores");
    TS.syncRows(tb, store, { row: "[data-tn-select]", page: "[data-tn-none]", attribute: "data-tn-select" });               // filas (visibles y filtradas)
    var shown = visibleBoxes(doc), head = r.querySelector("[data-tn-select-page]");
    var view = { querySelectorAll: function (s) { return s === "row" ? shown : (s === "page" && head ? [head] : []); } };
    var visible = TS.syncRows(view, store, { row: "row", page: "page", attribute: "data-tn-select" });                   // encabezado: SOLO lo visible
    var size = store.size(), bar = r.querySelector("[data-tn-selection-bar]");
    if (bar) bar.hidden = size === 0;
    var count = r.querySelector("[data-tn-selection-count]"), label = r.querySelector("[data-tn-selection-label]"), hint = r.querySelector("[data-tn-selection-hint]");
    if (count) count.textContent = size;
    if (label) label.textContent = size === 1 ? "colaborador seleccionado" : "colaboradores seleccionados";
    var outside = size - visible.visibleSelected;
    if (hint) hint.textContent = outside > 0 ? outside + " fuera de este filtro" : "";
    var nb = r.querySelector("[data-tn-notify]");
    if (nb && !nb.getAttribute("data-tn-busy")) {
      var blocked = r.getAttribute("data-simulacion") === "1" || r.getAttribute("data-excepciones-ok") !== "1";
      nb.disabled = size === 0 || blocked;                                      // simulación o registro de excepciones no verificable: sin envíos
      if (sim) nb.title = "Simulación de horas base: volvé a 40 h para notificar."; else nb.removeAttribute("title");
    }
  }
  // Fila → ese colaborador; encabezado → SOLO los elegibles visibles. `keepInPlace` compensa la barra que aparece/desaparece.
  function toggleSelection(doc, box) {
    var win = doc.defaultView || window;
    if (!store || !box || !win.dwTableSelection) return;
    var ids = box.hasAttribute("data-tn-select") ? [idOf(box)] : visibleBoxes(doc).map(idOf);
    win.dwTableSelection.keepInPlace(doc, box.closest ? box.closest("tr") : null, function () {
      if (box.checked) store.add(ids); else store.remove(ids);
      syncSelection(doc);
    });
  }
  function clearSelection(doc) { if (store) store.clear(); syncSelection(doc); }
  // Aviso del Hub (`.callout--feedback`, MISMO markup que el aviso de Compartir presentaciones): tono success / punch (atención) / warn (error) /
  // neutro (en curso). El contenedor `[data-dw-auto-dismiss]` lo vacía solo (initAutoDismissFeedback, app.js).
  function feedback(doc, title, text, tone) {
    var host = root(doc) && root(doc).querySelector("[data-tn-status]");
    if (!host) return;
    if (!title) { host.replaceChildren(); return; }
    var box = doc.createElement("div");
    box.className = "callout callout--feedback" + (tone ? " callout--" + tone : "");
    box.setAttribute("role", "status");
    var head = doc.createElement("div"); head.className = "callout__head";
    var icon = doc.createElement("span"); icon.className = "callout__icon"; icon.textContent = tone === "success" ? "✓" : tone ? "⚠️" : "…";
    var heading = doc.createElement("span"); heading.className = "callout__title"; heading.textContent = title;
    head.appendChild(icon); head.appendChild(heading); box.appendChild(head);
    if (text) { var body = doc.createElement("div"); body.className = "callout__body"; var p = doc.createElement("p"); p.textContent = text; body.appendChild(p); box.appendChild(body); }
    if (host.classList) host.classList.remove("is-dismissing");
    host.replaceChildren(box);
  }

  // ───────────── previsualización ─────────────
  // Previsualización: el diálogo se abre YA con el preloader del Hub (`LOADING`) y se completa al llegar la respuesta; error dentro del mismo
  // diálogo. Cada apertura lleva un número de pedido: una respuesta tardía de un pedido anterior, o con el diálogo ya cerrado, se descarta.
  var previewSeq = 0;
  function preview(doc, win, email) {
    var r = root(doc); if (!r) return;
    var dlg = doc.getElementById("tn-preview-dialog"); if (!dlg) return;
    var seq = ++previewSeq;
    var subj = dlg.querySelector("[data-tn-preview-subject]"), bodyEl = dlg.querySelector("[data-tn-preview-body]");
    var row = rowFor(doc, email), nombre = row ? row.getAttribute("data-tn-nombre") : email;
    if (subj) subj.textContent = nombre;
    if (bodyEl) bodyEl.innerHTML = LOADING;
    dlg.setAttribute("data-tn-preview-for", email);
    if (dlg.showModal && !dlg.open) dlg.showModal();
    function current() { return seq === previewSeq && dlg.open && dlg.getAttribute("data-tn-preview-for") === email; }
    function fail(text) {
      if (!current()) return;
      if (subj) subj.textContent = nombre;
      if (bodyEl) bodyEl.innerHTML = "";
      var box = doc.createElement("div"); box.className = "dashboard-empty"; box.setAttribute("data-error-kind", "source");
      var lab = doc.createElement("span"); lab.className = "dashboard-empty__label"; lab.textContent = "No se pudo generar la previsualización";
      var p = doc.createElement("p"); p.textContent = text;
      box.appendChild(lab); box.appendChild(p); if (bodyEl) bodyEl.appendChild(box);
    }
    Promise.resolve(win.dwGraphToken ? win.dwGraphToken() : "").then(function (token) {
      if (!current()) return;
      if (!token) { fail("No se pudo obtener autorización de Microsoft."); return; }
      var body = "email=" + encodeURIComponent(email) + "&semana=" + encodeURIComponent(r.getAttribute("data-semana") || "") +
                 "&graph_token=" + encodeURIComponent(token);                     // el modo (prueba) lo decide el servidor
      return win.fetch(PREVIEW_URL, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, d: d }; }); })
        .then(function (x) {
          if (!current()) return;                                                 // diálogo cerrado u otro colaborador: respuesta descartada
          if (!x.ok) { fail((x.d && x.d.error) || "Intentá de nuevo en unos minutos."); return; }
          if (subj) subj.textContent = "Asunto: " + x.d.subject + (x.d.recipient ? "  ·  Destinatario: " + x.d.recipient : "");
          if (bodyEl) {
            bodyEl.textContent = "";
            var frame = doc.createElement("iframe");
            frame.setAttribute("title", "Previsualización del correo");
            frame.style.width = "100%"; frame.style.height = "46vh"; frame.style.border = "0";
            bodyEl.appendChild(frame); frame.srcdoc = x.d.html;
          }
        });
    }).catch(function () { fail("Intentá de nuevo en unos minutos."); });
  }

  // ───────────── envío ─────────────
  function notify(doc, win) {
    var r = root(doc); if (!r) return;
    var emails = selected();
    if (!emails.length) return;
    if (r.getAttribute("data-simulacion") === "1") { feedback(doc, "No se envían recordatorios en simulación.", "Volvé a 40 h de base para notificar.", "punch"); return; }
    if (r.getAttribute("data-excepciones-ok") !== "1") { feedback(doc, "No se puede notificar.", "No se pudo verificar el registro de excepciones permanentes; reintentá en unos minutos.", "warn"); return; }
    var n = emails.length;
    var msg = (n === 1 ? "Vas a enviar un recordatorio de carga de horas al colaborador seleccionado."
                       : "Vas a enviar recordatorios de carga de horas a " + n + " colaboradores seleccionados.") +
              "\n\nCada correo se envía al email corporativo del colaborador. ¿Confirmás el envío?";
    var ask = win.dwConfirm ? win.dwConfirm({ title: "Enviar recordatorios", message: msg, confirmLabel: "Enviar", cancelLabel: "Cancelar", okClass: "btn-primary" })
                            : Promise.resolve(win.confirm(msg));
    Promise.resolve(ask).then(function (ok) { if (ok) doNotify(doc, win, r, emails); });
  }

  function doNotify(doc, win, r, emails) {
    var notifyBtn = r.querySelector("[data-tn-notify]"); if (notifyBtn) notifyBtn.disabled = true;
    Promise.resolve(win.dwGraphToken ? win.dwGraphToken() : "").then(function (token) {
      if (!token) { feedback(doc, "No se envió ningún recordatorio.", "No se pudo obtener autorización de Microsoft.", "warn"); if (notifyBtn) notifyBtn.disabled = false; return; }
      var parts = ["semana=" + encodeURIComponent(r.getAttribute("data-semana") || ""),
                   "confirm=yes", "graph_token=" + encodeURIComponent(token)];
      emails.forEach(function (e) { parts.push("emails=" + encodeURIComponent(e)); });
      parts.push("horas_base=" + encodeURIComponent(r.getAttribute("data-horas-base") || ""));     // el servidor rechaza si no es la regla vigente
      qsa(r, "[data-tn-feriado-opt]:checked").forEach(function (c) { parts.push("feriados_tipos=" + encodeURIComponent(c.value)); });
      parts.push("estados_presentes=1");
      estadosSel(r).forEach(function (s) { parts.push("estados=" + encodeURIComponent(s)); });
      return win.fetch(NOTIFY_URL, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: parts.join("&") })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, d: d }; }); })
        .then(function (x) { applyResults(doc, x); });
    }).catch(function () { feedback(doc, "No se pudo confirmar el resultado del envío.", "La conexión se interrumpió. No se reenvía automáticamente: verificá antes de reintentar.", "punch"); if (notifyBtn) notifyBtn.disabled = false; });
  }

  // Resultado del envío → badge de Solicitudes (`.solicitudes-status-badge--{tono}`): enviada verde · fallida rojo · incierta ámbar ·
  // deshabilitado gris; en modo prueba la aceptación usa el tono secundario azul. El texto sigue siendo explícito.
  var LABELS = { accepted: "Enviada", duplicate: "Ya enviada", uncertain: "Incierta", failed: "Fallida",
                 disabled: "Envío deshabilitado", config_incomplete: "Config. incompleta", error: "Error" };
  var TONES = { accepted: "green", duplicate: "green", uncertain: "amber", failed: "red", disabled: "grey", config_incomplete: "red", error: "red" };
  function setBadge(el, tone, text) {
    if (!el) return;
    el.className = "dashboard-status-badge solicitudes-status-badge solicitudes-status-badge--" + tone;
    el.textContent = text;
  }

  function applyResults(doc, x) {
    var r = root(doc);
    if (!x.ok) {
      feedback(doc, "No se envió ningún recordatorio.", (x.d && x.d.message) || "El envío fue bloqueado.", "warn");
      var nb = r && r.querySelector("[data-tn-notify]"); if (nb) nb.disabled = false;
      return;
    }
    (x.d.resultados || []).forEach(function (res) {
      var row = r.querySelector('[data-tn-email="' + (res.colaborador || "").replace(/"/g, "") + '"]');
      var cell = row && row.querySelector("[data-tn-notif-state]");
      var ok = res.status === "accepted" || res.status === "duplicate";
      // Texto corto (la columna es angosta y el badge recorta); el detalle va en el tooltip.
      setBadge(cell, TONES[res.status] || "grey", LABELS[res.status] || res.status);
      if (cell) cell.title = "Envío" + (res.recipient ? " · destinatario: " + res.recipient : "") +
                             (res.status === "uncertain" ? " · no reintentar sin verificar" : "") + (res.status === "accepted" ? " · Microsoft aceptó el envío (no confirma entrega)" : "");
    });
    var m = resultMessage(x.d);
    feedback(doc, m.title, m.text, m.tone);
    clearSelection(doc);
  }

  // Resumen funcional del lote (sin afirmar entrega: 202 = Microsoft ACEPTÓ el envío). Singular/plural; parcial si hay mezcla de resultados.
  function resultMessage(d) {
    var rs = d.resumen || {}, total = rs.total || 0, acc = rs.accepted || 0, dup = rs.duplicate || 0, fail = (rs.failed || 0) + (rs.error || 0) + (rs.config_incomplete || 0),
        unc = rs.uncertain || 0, dis = rs.disabled || 0, skipped = (d.skipped || []).length;
    var omit = skipped ? " " + skipped + (skipped === 1 ? " colaborador omitido" : " colaboradores omitidos") + " por no ser elegible." : "";
    if (total && acc === total) {
      return { tone: "success", title: total === 1 ? "Microsoft aceptó el envío del recordatorio." : "Microsoft aceptó el envío de " + total + " recordatorios.",
               text: omit.trim() };
    }
    if (total && dup === total) return { tone: "punch", title: total === 1 ? "El recordatorio ya se había enviado." : "Los recordatorios ya se habían enviado.", text: "No se reenvió." + omit };
    if (total && unc === total) return { tone: "punch", title: total === 1 ? "Envío sin confirmación de Microsoft." : "Envíos sin confirmación de Microsoft.", text: "No se reenvía automáticamente: verificá antes de reintentar." + omit };
    if (total && dis === total) return { tone: "punch", title: "El envío de correos está deshabilitado.", text: "No se envió ningún recordatorio." + omit };
    if (total && fail === total) return { tone: "warn", title: total === 1 ? "No se pudo enviar el recordatorio." : "No se pudieron enviar los recordatorios.", text: ("El servicio de correo rechazó el envío." + omit).trim() };
    var parts = [];
    if (acc) parts.push(acc + (acc === 1 ? " aceptado por Microsoft" : " aceptados por Microsoft"));
    if (dup) parts.push(dup + (dup === 1 ? " ya enviado" : " ya enviados"));
    if (unc) parts.push(unc + (unc === 1 ? " sin confirmación" : " sin confirmación"));
    if (fail) parts.push(fail + (fail === 1 ? " fallido" : " fallidos"));
    if (dis) parts.push(dis + (dis === 1 ? " no enviado (deshabilitado)" : " no enviados (deshabilitado)"));
    return { tone: "punch", title: "Envío parcial de " + total + (total === 1 ? " recordatorio." : " recordatorios."),
             text: parts.join(" · ") + "." + (unc ? " Los inciertos no se reenvían automáticamente." : "") + prueba + omit };
  }

  // ───────────── excepciones permanentes (mover / reincorporar) ─────────────
  // Persisten en servidor (registro durable); la UI mueve la MISMA fila entre tablas (sin re-descargar el CSV) y resincroniza contadores, KPIs,
  // filtros y selección. El envío vuelve a validar el registro en servidor: lo que muestra la UI nunca es la única barrera.
  function postForm(win, url, fields) {
    var body = Object.keys(fields).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(fields[k]); }).join("&");
    return win.fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body })
      .then(function (res) { return res.json().catch(function () { return {}; }).then(function (d) { return { ok: res.ok, d: d }; }); });
  }
  function rowFor(doc, email) {
    return qsa(root(doc), "tr[data-tn-email]").filter(function (tr) { return tr.getAttribute("data-tn-email") === email; })[0] || null;
  }
  function setRowMode(tr, excepted, title) {
    var add = tr.querySelector("[data-tn-exc-add]"), rem = tr.querySelector("[data-tn-exc-remove]"), prev = tr.querySelector("[data-tn-preview]");
    // `.table-action-btn` fija su `display`: el atributo `hidden` no la oculta → visibilidad por `style.display` (igual que las filas filtradas).
    if (add) add.style.display = excepted ? "none" : "";
    if (rem) rem.style.display = excepted ? "" : "none";
    if (prev) prev.style.display = excepted ? "none" : "";
    qsa(tr, ".hc-select-col input").forEach(function (cb) { cb.checked = false; cb.hidden = excepted; cb.disabled = excepted || !cb.hasAttribute("data-tn-select"); });
    tr.hidden = false;
    var st = tr.querySelector("[data-tn-notif-state]");
    if (st) {
      setBadge(st, "grey", excepted ? "Exceptuado" : (st.getAttribute("data-tn-notif-default") || "Pendiente"));
      if (excepted && title) st.title = title; else st.removeAttribute("title");
    }
    var xb = tr.querySelector("[data-tn-exc-badge]"), xd = tr.querySelector("[data-tn-exc-dash]");
    if (xb) xb.style.display = excepted ? "" : "none";
    if (xd) xd.style.display = excepted ? "none" : "";
    tr.style.display = "";
    if (tr.classList) tr.classList.remove("is-selected");
  }
  function insertSorted(tb, tr) {
    var name = (tr.getAttribute("data-tn-nombre") || "").toLowerCase();
    var next = qsa(tb, "tr[data-tn-email]").filter(function (x) { return (x.getAttribute("data-tn-nombre") || "").toLowerCase() > name; })[0];
    tb.insertBefore(tr, next || null);
  }
  function excAdd(doc, win, email) {
    var tr = rowFor(doc, email); if (!tr) return;
    var nombre = tr.getAttribute("data-tn-nombre") || email;
    var msg = nombre + " (" + email + ") dejará de recibir recordatorios de carga de horas en TODAS las semanas, hasta que lo reincorpores.\nNo se modifica el CSV ni el padrón.";
    var ask = win.dwConfirm ? win.dwConfirm({ title: "Mover a excepciones", message: msg, confirmLabel: "Mover a excepciones", cancelLabel: "Cancelar" })
                            : Promise.resolve(win.confirm(msg));
    Promise.resolve(ask).then(function (ok) {
      if (!ok) return;
      return postForm(win, EXC_ADD_URL, { email: email, nombre: nombre }).then(function (x) {
        if (!x.ok || !x.d || x.d.status !== "ok") { feedback(doc, "No se pudo incorporar a " + nombre + " a Excepciones.", (x.d && x.d.message) || "No se aplicó ningún cambio.", "warn"); return; }
        var e = x.d.entry || {};
        setRowMode(tr, true, "Excepción permanente" + (e.added_by ? " · alta por " + e.added_by : ""));
        insertSorted(tbody(doc, "excepciones"), tr);
        feedback(doc, nombre + " se incorporó a Excepciones.", "", "success");
        refreshKpis(doc); applyFilters(doc);
      });
    }).catch(function () { feedback(doc, "No se pudo incorporar a " + nombre + " a Excepciones.", "Intentá de nuevo en unos minutos. No se aplicó ningún cambio.", "warn"); });
  }
  function excRemove(doc, win, email) {
    var tr = rowFor(doc, email); if (!tr) return;
    var nombre = tr.getAttribute("data-tn-nombre") || email;
    postForm(win, EXC_REMOVE_URL, { email: email }).then(function (x) {
      if (!x.ok || !x.d || x.d.status !== "ok") { feedback(doc, "No se pudo reincorporar a " + nombre + ".", (x.d && x.d.message) || "No se aplicó ningún cambio.", "warn"); return; }
      if (tr.hasAttribute("data-tn-fuera-padron")) {                   // sin fila calculada esta semana: sale del registro, no hay datos que mostrar
        tr.parentNode.removeChild(tr);
        feedback(doc, nombre + " salió de Excepciones.", "Volverá al seguimiento de Carga de Horas cuando figure en el padrón de la semana.", "success");
      } else {
        setRowMode(tr, false);
        tbody(doc, "colaboradores").appendChild(tr);
        feedback(doc, nombre + " volvió al seguimiento de Carga de Horas.", "", "success");
      }
      refreshKpis(doc); applyFilters(doc);
    }).catch(function () { feedback(doc, "No se pudo reincorporar a " + nombre + ".", "Intentá de nuevo en unos minutos. No se aplicó ningún cambio.", "warn"); });
  }

  // ───────────── vigencia por vista: solo la ÚLTIMA vista elegida puede pedir o pintar en #staffing-rows ─────────────
  // La vista de cada request sale de su ruta (/asignaciones/… vs /carga-horas/…). Un request de otra vista (p. ej. el token de una pestaña
  // anterior que se resolvió tarde) no se emite; una respuesta o error tardío de otra vista no se pinta. En captura sobre `document`, antes de
  // los listeners de `body` (preloader, barra de progreso): un request descartado no llega a encender la barra.
  function viewOf(detail) {
    var path = String((detail && ((detail.pathInfo && detail.pathInfo.requestPath) || (detail.requestConfig && detail.requestConfig.path))) || "");
    if (path.indexOf("/carga-horas/") >= 0) return "carga-horas";
    if (path.indexOf("/staffing/asignaciones/data") >= 0) return "asignaciones";
    return "";
  }
  function staleForRows(detail) {
    var tgt = detail && detail.target, v = viewOf(detail);
    return !!(tgt && tgt.id === "staffing-rows" && v && v !== currentView);
  }
  function viewGuard(doc) {
    doc.addEventListener("htmx:beforeRequest", function (e) {
      if (staleForRows(e.detail)) { e.preventDefault(); e.stopPropagation(); }
    }, true);
    doc.addEventListener("htmx:beforeSwap", function (e) {
      if (staleForRows(e.detail)) { e.detail.shouldSwap = false; e.stopPropagation(); }
    }, true);
    ["htmx:responseError", "htmx:sendError"].forEach(function (name) {
      doc.addEventListener(name, function (e) { if (staleForRows(e.detail)) e.stopPropagation(); }, true);   // error tardío: no toca la vista activa
    });
  }

  // ───────────── cableado (delegado en document; sobrevive a los swaps) ─────────────
  function wire(doc, win) {
    // Botonera en CAPTURA: la vista activa cambia ANTES de que htmx emita el request del botón (htmx escucha en el propio elemento), así
    // `viewGuard` ya considera vigente la vista nueva y descarta cualquier request/respuesta de la anterior.
    doc.addEventListener("click", function (ev) {
      var b = ev.target && ev.target.closest ? ev.target.closest("[data-opsb-view]") : null;
      if (b) toggleView(doc, b);
    }, true);
    doc.addEventListener("click", function (ev) {
      var t = ev.target, el = t && t.closest ? t.closest("[data-opsb-view],[data-tn-clear-filters],[data-tn-clear-selection],[data-tn-notify],[data-tn-preview],[data-tn-exc-add],[data-tn-exc-remove]") : null;
      if (!el) return;
      if (el.hasAttribute("data-opsb-view")) return;                       // lo atiende el listener en captura (abajo)
      var r = root(doc); if (!r) return;
      if (el.hasAttribute("data-tn-clear-filters")) {
        var sEl = r.querySelector("[data-tn-search]"), eEl = r.querySelector("[data-tn-filter-estado]");
        if (sEl) sEl.value = ""; if (eEl) eEl.value = "faltantes";
        var dEl = r.querySelector("[data-tn-filter-disp]"); if (dEl) dEl.value = dEl.getAttribute("data-tn-disp-default") || "Full Time";
        var def = (r.getAttribute("data-estados-default") || "Activo").split("|");
        qsa(r, "[data-tn-estado-opt]").forEach(function (c) { c.checked = def.indexOf(c.value) >= 0; });
        applyFilters(doc); return;
      }
      if (el.hasAttribute("data-tn-clear-selection")) { clearSelection(doc); return; }
      if (el.hasAttribute("data-tn-notify")) { notify(doc, win); return; }
      if (el.hasAttribute("data-tn-preview")) { preview(doc, win, el.getAttribute("data-tn-preview")); return; }
      if (el.hasAttribute("data-tn-exc-add")) { excAdd(doc, win, el.getAttribute("data-tn-exc-add")); return; }
      if (el.hasAttribute("data-tn-exc-remove")) { excRemove(doc, win, el.getAttribute("data-tn-exc-remove")); return; }
    });
    doc.addEventListener("input", function (ev) {
      var t = ev.target; if (t && t.matches && t.matches("[data-tn-search]")) applyFilters(doc);
    });
    doc.addEventListener("change", function (ev) {
      var t = ev.target; if (!t || !t.matches) return;
      if (t.matches("[data-tn-feriado-opt]")) {                                // tipos de feriado → recálculo en servidor (misma recarga del form)
        var rf = root(doc), rbf = rf && rf.querySelector("[data-tn-reload]");
        if (rbf) rbf.click();
        return;
      }
      if (t.matches("[data-tn-horas-base]")) {
        var rh = root(doc), hv = rh && rh.querySelector("[data-tn-horas-base-value]"), rbh = rh && rh.querySelector("[data-tn-reload]");
        if (hv && rbh && t.value && hv.value !== t.value) { hv.value = t.value; rbh.click(); }
        return;
      }
      if (t.matches("[data-tn-filter-estado], [data-tn-filter-disp]")) {
        var r0 = root(doc);                                                       // con una consulta en curso / tras error: reconsultar (la última gana)
        if (r0 && (r0.hasAttribute("data-tn-loading") || r0.hasAttribute("data-tn-stale"))) { var rb0 = r0.querySelector("[data-tn-reload]"); if (rb0) { rb0.click(); return; } }
        applyFilters(doc);
      }
      else if (t.matches("[data-tn-estado-opt]")) {
        var rr = root(doc);
        // Inactivo / «Sin estado» necesitan el historial de Inactivos: UNA recarga del panel (el form lleva semana + estados elegidos).
        if (rr && rr.getAttribute("data-estados-ok") === "1" && (rr.hasAttribute("data-tn-loading") || rr.hasAttribute("data-tn-stale") || (rr.getAttribute("data-inactivos") !== "1" && needsInactivos(estadosSel(rr))))) {
          var rb = rr.querySelector("[data-tn-reload]"); if (rb) { rb.click(); return; }
        }
        applyFilters(doc);
      }
      else if (t.matches("[data-tn-select], [data-tn-select-page]")) { toggleSelection(doc, t); }
    });
    // Reprocesamiento disparado DESDE los filtros (Semana / recarga por Estado): retira los resultados vigentes y muestra el preloader del Hub
    // en su lugar; los filtros (y los avisos de contexto de arriba) siguen visibles. La respuesta reemplaza el panel completo (htmx).
    function resultsOf(r) {
      var out = [], after = false;
      [].slice.call(r.children).forEach(function (el) {
        if (el.matches && el.matches("[data-tn-filters]")) { after = true; return; }
        if (after && !(el.matches && el.matches("dialog, [data-tn-status], [data-tn-loading-slot]"))) out.push(el);
      });
      return out;
    }
    function fromFilters(e) { var elt = e.detail && e.detail.elt; return !!(elt && elt.closest && elt.closest("[data-tn-filters]") && root(doc)); }
    function slot(r) {
      var s = r.querySelector("[data-tn-loading-slot]");
      if (!s) { s = doc.createElement("div"); s.setAttribute("data-tn-loading-slot", ""); var f = r.querySelector("[data-tn-filters]"); f.parentNode.insertBefore(s, f.nextSibling); }
      return s;
    }
    doc.body.addEventListener("htmx:beforeRequest", function (e) {
      if (!fromFilters(e)) return;
      var r = root(doc);
      r.setAttribute("data-tn-loading", "");
      resultsOf(r).forEach(function (el) { el.style.display = "none"; });               // nada de la consulta anterior queda a la vista
      if (store) store.clear();                                                // la selección vieja no sobrevive a un universo nuevo
      slot(r).innerHTML = LOADING;
    });
    function loadFailed(e) {
      if (!fromFilters(e)) return;
      var r = root(doc); r.removeAttribute("data-tn-loading"); r.setAttribute("data-tn-stale", "");   // tras un error, el próximo cambio vuelve a consultar
      slot(r).innerHTML = '<div class="dashboard-empty" data-error-kind="source"><span class="dashboard-empty__label">No disponible</span>' +
        '<p>No se pudo actualizar la Carga de Horas con los filtros elegidos. Volvé a intentar; los resultados anteriores ya no corresponden a esta selección.</p></div>';
    }
    doc.body.addEventListener("htmx:responseError", loadFailed);
    doc.body.addEventListener("htmx:sendError", loadFailed);

    // Tras cada swap de #staffing-rows: refrescar hero/metadatos, filtros y selección de la vista cargada.
    doc.body.addEventListener("htmx:afterSwap", function (e) {
      var tgt = e.detail && e.detail.target;
      if (!tgt || tgt.id !== "staffing-rows") return;
      if (currentView === "carga-horas" || root(doc)) { store = null; fillHeroMeta(doc); applyFilters(doc); }
      else hideHeroMeta(doc);
    });
  }

  if (typeof window !== "undefined" && typeof document !== "undefined" && !window.__opsTimenotesWired) {
    window.__opsTimenotesWired = true;
    viewGuard(document);
    wire(document, window);
  }
})();

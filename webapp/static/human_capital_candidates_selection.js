/* human_capital_candidates_selection.js — CCHH · Candidatos: selección múltiple + «Compartir perfiles».

   QUÉ HACE
   · Mantiene un conjunto de `profile_id` (UUID opacos, NADA más: ni nombres ni datos personales) en `sessionStorage` (por pestaña, muere con la sesión del navegador; jamás
     localStorage). Persiste entre páginas y filtros: el panel del listado se reemplaza entero en cada filtro/orden/página (HTMX), así que el estado vive AFUERA del fragmento y
     después de cada swap se re-marcan las casillas visibles.
   · Casilla de encabezado: selecciona/deselecciona SOLO las filas de la página visible; es tri-estado (marcada / indeterminada / vacía).
   · Barra contextual (oculta con 0 seleccionados; el desplazamiento al aparecer/desaparecer se compensa para no desviar el clic siguiente): contador, «Compartir perfiles», «Descargar CVs» y «✕ Limpiar» (el mismo componente que el de los filtros).
   · «Compartir perfiles»: pide al servidor (POST, solo ids) el contenido del diálogo —nombres y rutas de las FICHAS resueltos en servidor— y arma el mensaje con el origen del
     propio navegador. «Copiar mensaje» es una acción explícita del usuario que no registra nada; «Enviar» manda el correo institucional tras una confirmación explícita.

   LÍMITES
   · Compartir: hasta MAX_SHARE perfiles (el servidor valida igual: esta cota es solo UX).

   Sin dependencias; se carga una sola vez desde `human_capital/profiles.html` y delega eventos en `document` (sobrevive a los swaps). Expone `window.__cchhSelection` para pruebas. */
(function () {
  "use strict";

  var STORAGE_KEY = "cchh-candidatos-selection";
  var STORAGE_VERSION = 1;
  var ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
  var MAX_STORED = 2000;
  var MAX_SHARE = 25;
  var SHARE_URL = "/cchh/candidatos/compartir";
  var DIRECTORY_URL = "/cchh/candidatos/compartir/destinatarios";       // búsqueda EN VIVO del directorio (token delegado)
  var SEND_URL = "/cchh/candidatos/compartir/enviar";                   // envío MANUAL por el buzón institucional
  var CATALOG_URL = "/cchh/candidatos/compartir/catalogo";              // proveedor transitorio «allowlist»: catálogo una vez por apertura
  var MIN_QUERY = 3;
  var SEARCH_DEBOUNCE = 250;

  // ───────────────────────── almacén de la selección (solo ids) ─────────────────────────
  function createStore(storage) {
    return window.dwTableSelection.createStore(storage, {key: STORAGE_KEY, idPattern: ID_RE, max: MAX_STORED});
  }

  // ───────────────────────── mensaje (HTML + texto plano) ─────────────────────────
  var SKIP = "__skip";

  function escapeHtml(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Misma regla que `human_capital_share.safe_cv_url` (servidor): https + *.sharepoint.com, sin credenciales ni caracteres peligrosos. El servidor ya valida; acá se vuelve a validar antes de armar un href.
  function safeCvUrl(url) {
    if (typeof url !== "string" || !url || url.length > 2048 || /[\s\x00-\x1f\x7f<>"'`\\]/.test(url)) return null;
    var m = /^https:\/\/([^\/?#]+)/i.exec(url);
    if (!m || m[1].indexOf("@") >= 0) return null;
    var host = m[1].replace(/:\d+$/, "").toLowerCase();
    return host.length > 15 && host.slice(-15) === ".sharepoint.com" && host.charAt(0) !== "." ? url : null;
  }

  // Filas de la vista previa. ÚNICA fuente de verdad de qué se comparte: con VARIOS CVs nunca se elige uno por cuenta propia (hay que elegir, o «No incluir»); con un único CV con enlace válido no hay
  // ambigüedad; sin CV o sin enlace válido el candidato se informa y no se incluye.
  function rowsModel(items, choice) {
    return (items || []).map(function (item) {
      var docs = item.docs || [];
      var linkable = docs.filter(function (d) { return safeCvUrl(d.url); });
      var row = { id: item.id, name: item.name, multi: docs.length > 1 && linkable.length > 0, options: [], selected: "", url: null, kind: "" };
      if (linkable.length === 0) { row.kind = docs.length ? "no_link" : "no_cv"; return row; }
      if (docs.length === 1) { row.kind = "linked"; row.url = linkable[0].url; return row; }
      row.options = docs.map(function (d) { return { id: d.id, label: d.label, disabled: !safeCvUrl(d.url) }; });
      var picked = choice && choice[item.id];
      if (picked === SKIP) { row.kind = "skipped"; row.selected = SKIP; return row; }
      var doc = linkable.filter(function (d) { return d.id === picked; })[0];
      if (doc) { row.kind = "linked"; row.url = doc.url; row.selected = doc.id; } else { row.kind = "pending"; }
      return row;
    });
  }

  function resolveShare(items, choice) {
    var rows = rowsModel(items, choice), model = { included: [], excluded: [], pending: [] };
    rows.forEach(function (r) {
      if (r.kind === "linked") model.included.push({ name: r.name, url: r.url });
      else if (r.kind === "pending") model.pending.push(r.name);
      else model.excluded.push({ name: r.name, reason: r.kind });
    });
    return model;
  }

  function paragraph(text) { return "<p>" + escapeHtml(text).replace(/\r?\n/g, "<br>") + "</p>"; }

  // Texto plano (fallback del portapapeles y cuerpo del correo enviado): nombre y URL COMPLETA de cada CV.
  function buildPlain(model, intro, outro) {
    var lines = model.included.map(function (n) { return "- " + n.name + ": " + n.url; });
    return [String(intro || "")].concat([""], lines, [""], [String(outro || "")]).join("\n");
  }

  // HTML (portapapeles `text/html`): el nombre es el hipervínculo al CV; sin URLs largas a la vista. Todo valor va escapado (nombres y atributos).
  function buildHtml(model, intro, outro) {
    var list = model.included.map(function (n) { return '<li><a href="' + escapeHtml(n.url) + '">' + escapeHtml(n.name) + "</a></li>"; }).join("");
    return (intro ? paragraph(intro) : "") + "<ul>" + list + "</ul>" + (outro ? paragraph(outro) : "");
  }


  // ───────────────────────── sincronización de la interfaz ─────────────────────────
  // Al aparecer/desaparecer la barra, todo lo que está debajo (la tabla) se corre su alto: el próximo clic caería en otra fila. `keepInPlace` mide dónde estaba el control accionado, aplica el
  // cambio y reubica el desplazamiento del contenedor para que ese control quede exactamente donde estaba (si no hay nada que compensar, no hace nada).
  function keepInPlace(doc, anchor, change) { return window.dwTableSelection.keepInPlace(doc, anchor, change); }

  function syncUI(doc, store) {
    var selectedRows = window.dwTableSelection.syncRows(doc, store);
    var boxes = selectedRows.boxes, visibleSelected = selectedRows.visibleSelected;
    var total = store.size();
    [].slice.call(doc.querySelectorAll("[data-hc-selection-bar]")).forEach(function (bar) {
      bar.hidden = total === 0;                                                             // sin selección no ocupa lugar; el desplazamiento al aparecer/desaparecer lo compensa `keepInPlace`
      var count = bar.querySelector("[data-hc-selection-count]");
      if (count) count.textContent = String(total);
      var label = bar.querySelector("[data-hc-selection-label]");
      if (label) label.textContent = total === 1 ? "seleccionado" : "seleccionados";
      var hint = bar.querySelector("[data-hc-selection-hint]");
      if (hint) {
        var outside = total - visibleSelected;
        var parts = [];
        if (outside > 0) parts.push(outside + (outside === 1 ? " fuera de la vista actual (otra página o filtro)" : " fuera de la vista actual (otras páginas o filtros)"));
        if (total > MAX_SHARE) parts.push("Compartir admite hasta " + MAX_SHARE + " perfiles");
        hint.textContent = parts.join(" · ");
      }
      var download = bar.querySelector("[data-hc-download-open]");
      if (download) {
        download.disabled = total === 0 || total > MAX_SHARE;
        download.setAttribute("title", total > MAX_SHARE ? "Se pueden descargar CVs de hasta " + MAX_SHARE + " candidatos por vez" : "");
      }
      var consolidate = bar.querySelector("[data-hc-consolidate-open]");
      if (consolidate) {
        consolidate.disabled = total !== 2;                                                    // «Consolidar perfiles» (solo el dueño): exactamente dos
        consolidate.setAttribute("title", total === 2 ? "" : "Seleccioná exactamente dos perfiles para consolidar");
      }
      var share = bar.querySelector("[data-hc-share-open]");
      if (share) {
        share.disabled = total === 0 || total > MAX_SHARE;
        share.setAttribute("title", total > MAX_SHARE ? "Se pueden compartir hasta " + MAX_SHARE + " perfiles por vez" : "");
      }
    });
  }

  // ───────────────────────── diálogo de compartir ─────────────────────────
  var shareState = { items: [], choice: {}, recipients: [], directoryEnabled: false, maxRecipients: 20, operationId: "", sending: false, sent: false, _searchSeq: 0 };

  function shareRoot(doc) { var c = doc.getElementById("hc-share-content"); return c && c.querySelector("[data-share-root]"); }
  // Los campos se buscan en TODO el contenedor del diálogo (no solo en `data-share-root`): las acciones —«Copiar mensaje», «Abrir Outlook»— y los JSON de datos viven fuera del bloque `root`.
  function shareField(doc, name) { var c = doc.getElementById("hc-share-content"); return c && shareRoot(doc) ? c.querySelector("[" + name + "]") : null; }
  function fieldValue(doc, name) { var f = shareField(doc, name); return f ? f.value : ""; }

  var NOTE = { no_cv: "sin CV cargado (no se incluye)", no_link: "su CV no tiene enlace disponible (no se incluye)", skipped: "omitido (no se incluye)", pending: "elegí qué CV compartir" };

  function renderList(doc, rows, focusId) {
    var list = shareField(doc, "data-share-list");
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    rows.forEach(function (row) {
      var li = doc.createElement("li");
      var name = doc.createElement("span");
      name.textContent = row.name;
      li.appendChild(name);
      if (row.kind === "linked") {
        // El enlace real no se muestra (URLs largas): se ve el marcador {Link}, que abre ESE CV para poder comprobarlo. Es de solo lectura: viene del modelo, nunca de lo que se escriba en el mensaje.
        var colon = doc.createElement("span");
        colon.textContent = ": ";
        li.appendChild(colon);
        var a = doc.createElement("a");
        a.textContent = "{Link}";
        a.setAttribute("href", row.url); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer");
        a.setAttribute("title", "Abrir el CV de " + row.name + " en SharePoint (nueva pestaña)");
        if (a.classList) a.classList.add("hc-link-marker");
        li.appendChild(a);
      } else if (li.classList) {
        li.classList.add("is-muted");
      }
      if (NOTE[row.kind]) {
        var note = doc.createElement("span");
        note.textContent = " — " + NOTE[row.kind];
        if (note.classList) note.classList.add("hc-share-note");
        li.appendChild(note);
      }
      if (row.multi) {
        var select = doc.createElement("select");
        select.setAttribute("data-share-choose", row.id);
        select.setAttribute("aria-label", "CV a compartir de " + row.name);
        var opts = [{ id: "", label: "Elegir CV…", disabled: false }].concat(row.options, [{ id: SKIP, label: "No incluir a este candidato", disabled: false }]);
        opts.forEach(function (o) {
          var opt = doc.createElement("option");
          opt.setAttribute("value", o.id);
          opt.textContent = o.label + (o.disabled ? " (sin enlace disponible)" : "");
          if (o.disabled) opt.disabled = true;
          select.appendChild(opt);
        });
        select.value = row.selected;
        li.appendChild(select);
      }
      list.appendChild(li);
    });
    if (focusId) { var again = list.querySelector('[data-share-choose="' + focusId + '"]'); if (again && again.focus) again.focus(); }
  }

  // Altura de un campo de texto según su contenido (sin barras de desplazamiento internas: el mensaje se ve completo).
  function autosize(field) {
    if (!field || !field.style) return;
    field.style.height = "auto";
    if (field.scrollHeight) field.style.height = field.scrollHeight + "px";
  }

  function setNote(node, text) { if (node) { node.hidden = !text; node.textContent = text || ""; } }

  function shareReady(model) { return model.pending.length === 0 && model.included.length > 0; }

  // «Copiar mensaje» solo se habilita con el mensaje completo (todos los CVs elegidos o excluidos).
  function refreshCopy(doc) {
    var copy = shareField(doc, 'data-share-copy="mensaje"');
    if (copy) copy.disabled = !shareReady(resolveShare(shareState.items, shareState.choice));
  }

  // ── Saludo personalizado ──
  // El saludo es el texto del campo de saludo hasta su PRIMERA coma («Hola,» / «Hola Brian,»). Un destinatario con nombre verificado (padrón
  // corporativo o directorio: lo resuelve el servidor, nunca se deduce del alias) → «Hola <Nombre>,»; cero, varios o sin nombre → «Hola,».
  // Solo se reemplaza el saludo si sigue siendo el último generado automáticamente: si el usuario lo cambió, se respeta; el resto del texto
  // (lo que sigue a la coma) nunca se toca. Es presentación: el envío valida destinatarios en el servidor como siempre.
  function firstName(person) {
    var name = String((person && person.name) || "").trim();
    if (!name || name.toLowerCase() === String(person.email || "").toLowerCase() || name.indexOf("@") !== -1) return "";
    return name.split(/\s+/)[0];
  }
  function greetingFor(recipients) {
    var first = recipients.length === 1 ? firstName(recipients[0]) : "";
    return first ? "Hola " + first + "," : "Hola,";
  }
  function currentGreeting(text) { var i = String(text || "").indexOf(","); return i === -1 ? null : text.slice(0, i + 1); }
  function applyGreeting(doc) {
    var intro = shareField(doc, "data-share-intro");
    if (!intro || shareState.greeting === null) return;
    if (currentGreeting(intro.value) !== shareState.greeting) { shareState.greeting = null; return; }      // lo editó el usuario: se respeta
    var next = greetingFor(shareState.recipients);
    if (next === shareState.greeting) return;
    intro.value = next + intro.value.slice(shareState.greeting.length);
    shareState.greeting = next;
  }

  function refreshShare(doc, focusId) {
    var model = resolveShare(shareState.items, shareState.choice);
    renderList(doc, rowsModel(shareState.items, shareState.choice), focusId);
    var pending = shareField(doc, "data-share-pending");
    if (model.pending.length) setNote(pending, "Falta elegir el CV de " + model.pending.length + (model.pending.length === 1 ? " candidato" : " candidatos") + " (o «No incluir a este candidato»).");
    else if (model.included.length === 0) setNote(pending, "Ningún candidato tiene un CV con enlace disponible para compartir.");
    else setNote(pending, "");
    refreshCopy(doc);
    refreshSend(doc);
  }

  function status(doc, text) { var s = shareField(doc, "data-share-status"); if (s) { s.textContent = text || ""; s.hidden = !text; } }

  function initShare(doc, store, win) {
    var root = shareRoot(doc);
    if (!root) return;
    var itemsEl = shareField(doc, "data-share-items"), items = [];
    try { items = itemsEl ? JSON.parse(itemsEl.textContent) : []; } catch (e) { items = []; }
    var unavailable = [];
    try { var u = shareField(doc, "data-share-unavailable"); unavailable = u ? JSON.parse(u.textContent) : []; } catch (e) { unavailable = []; }
    if (unavailable.length) { store.remove(unavailable); syncUI(doc, store); }                    // un perfil dado de baja se saca de la selección: el contador no miente
    var maxR = parseInt(root.getAttribute("data-max-recipients"), 10);
    shareState = { items: items, choice: {}, recipients: [], directoryEnabled: !!root.getAttribute("data-directory-enabled"),
                   provider: root.getAttribute("data-recipient-provider") === "allowlist" ? "allowlist" : "directory", catalog: null, greeting: null,
                   maxRecipients: isNaN(maxR) ? 20 : maxR, operationId: newOperationId(win), sending: false, sent: false, _searchSeq: 0 };
    shareField(doc, "data-share-subject").value = root.getAttribute("data-default-subject") || "";
    shareField(doc, "data-share-intro").value = root.getAttribute("data-intro") || "";
    shareState.greeting = currentGreeting(shareField(doc, "data-share-intro").value) === "Hola," ? "Hola," : null;      // solo se gestiona el saludo genérico de la plantilla
    shareField(doc, "data-share-outro").value = root.getAttribute("data-outro") || "";
    autosize(shareField(doc, "data-share-intro"));
    autosize(shareField(doc, "data-share-outro"));
    var search = shareField(doc, "data-share-search");
    if (search && !shareState.directoryEnabled) search.disabled = true;                            // sin permiso de directorio: el selector queda inerte (el aviso ya está en el partial)
    renderChips(doc);
    refreshShare(doc);
    if (shareState.provider === "allowlist" && shareState.directoryEnabled) loadCatalog(doc, win);
  }

  // «allowlist»: UNA lectura por apertura (catálogo = HUMAN_CAPITAL_ALLOWED_USERS + nombres del padrón con el token base de lectura). Si el
  // token o SharePoint fallan, el servidor devuelve igual los correos; si la ruta misma falla, la búsqueda informa y se puede reabrir.
  function loadCatalog(doc, win) {
    var opened = shareState;
    var tokenFn = win.dwGraphToken;
    return Promise.resolve(tokenFn ? tokenFn() : "").catch(function () { return ""; }).then(function (token) {
      return win.fetch(CATALOG_URL, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" },
                                      body: "graph_token=" + encodeURIComponent(token || "") });
    }).then(function (res) { return res.ok ? res.json() : { recipients: [] }; }).then(function (data) {
      if (shareState !== opened) return;                                                         // el diálogo se reabrió mientras cargaba
      shareState.catalog = (data && data.recipients) || [];
      var input = shareField(doc, "data-share-search");
      if (input && (input.value || "").trim().length >= MIN_QUERY) runSearch(doc, win);
    }).catch(function () { if (shareState === opened) shareState.catalog = []; });
  }

  function fold(text) { return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }

  // Coincidencia en memoria: nombre, apellido (cualquier palabra del nombre), alias (parte local) o correo; sin distinguir acentos.
  function catalogMatches(q) {
    var needle = fold(q);
    return (shareState.catalog || []).filter(function (p) {
      var email = fold(p.email), alias = email.split("@")[0];
      return fold(p.name).indexOf(needle) !== -1 || email.indexOf(needle) !== -1 || alias.indexOf(needle) !== -1;
    });
  }

  // ───────────────────────── destinatarios: directorio en vivo + chips + envío ─────────────────────────
  function newOperationId(win) {
    try { if (win.crypto && win.crypto.randomUUID) return win.crypto.randomUUID().replace(/-/g, ""); } catch (e) { /* sigue al fallback */ }
    return "op" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);              // id ≥8, [A-Za-z0-9_-] (ver OPERATION_ID_RE en el servidor)
  }

  function sendStatus(doc, text, warn) {
    var s = shareField(doc, "data-share-send-status");
    if (!s) return;
    s.textContent = text || ""; s.hidden = !text;
    if (s.classList) { if (warn) s.classList.add("field-hint--warn"); else s.classList.remove("field-hint--warn"); }
  }

  function hasRecipient(email) {
    var key = String(email || "").toLowerCase();
    return shareState.recipients.some(function (r) { return r.email.toLowerCase() === key; });
  }

  function renderChips(doc) {
    var list = shareField(doc, "data-share-chips");
    if (!list) return;
    list.textContent = "";
    shareState.recipients.forEach(function (r) {
      var li = doc.createElement("li");
      li.className = "hc-share-chip";
      var name = doc.createElement("span");
      name.className = "hc-share-chip__label";
      name.textContent = r.name && r.name !== r.email ? (r.name + " · " + r.email) : r.email;
      li.appendChild(name);
      var btn = doc.createElement("button");
      btn.type = "button"; btn.className = "hc-share-chip__remove";
      btn.setAttribute("data-share-chip-remove", r.email);
      btn.setAttribute("aria-label", "Quitar a " + (r.name || r.email));
      btn.title = "Quitar";
      btn.textContent = "×";
      li.appendChild(btn);
      list.appendChild(li);
    });
  }

  function addRecipient(doc, person) {
    if (!person || !person.email || hasRecipient(person.email)) return;
    if (shareState.recipients.length >= shareState.maxRecipients) { sendStatus(doc, "Se pueden agregar hasta " + shareState.maxRecipients + " destinatarios.", true); return; }
    shareState.recipients.push({ name: person.name || person.email, email: person.email });
    renderChips(doc);
    applyGreeting(doc);
    refreshSend(doc);
  }

  function removeRecipient(doc, email) {
    var key = String(email || "").toLowerCase();
    shareState.recipients = shareState.recipients.filter(function (r) { return r.email.toLowerCase() !== key; });
    renderChips(doc);
    applyGreeting(doc);
    refreshSend(doc);
  }

  function showResults(doc, show) {
    var box = shareField(doc, "data-share-results"), input = shareField(doc, "data-share-search");
    if (box) box.hidden = !show;
    if (input) input.setAttribute("aria-expanded", show ? "true" : "false");
  }

  function renderResults(doc, rows) {
    var listEl = shareField(doc, "data-share-results-list"), hint = shareField(doc, "data-share-results-hint");
    if (!listEl) return;
    listEl.textContent = "";
    rows.forEach(function (p) {
      var opt = doc.createElement("div");
      opt.setAttribute("role", "option");
      opt.setAttribute("data-share-pick", p.email);
      opt.setAttribute("data-share-pick-name", p.name || p.email);
      opt.setAttribute("aria-selected", hasRecipient(p.email) ? "true" : "false");
      if (p.name && p.name !== p.email) {                                                         // «Nombre Apellido» y debajo el correo
        var nameEl = doc.createElement("span"); nameEl.className = "hc-share-option__name"; nameEl.textContent = p.name;
        var mailEl = doc.createElement("span"); mailEl.className = "hc-share-option__email"; mailEl.textContent = p.email;
        opt.appendChild(nameEl); opt.appendChild(mailEl);
      } else {
        opt.textContent = p.email;
      }
      listEl.appendChild(opt);
    });
    if (hint) { hint.textContent = rows.length ? "" : "Sin coincidencias en el directorio."; hint.hidden = rows.length > 0; }
  }

  // Proveedor «allowlist» (catálogo gobernado transitorio): el servidor resuelve y valida sin Microsoft → no se pide token delegado.
  function directoryToken(win) {
    if (shareState.provider === "allowlist") return Promise.resolve("");
    var tokenFn = win.dwHumanCapitalDirectoryToken;
    return Promise.resolve(tokenFn ? tokenFn() : "");
  }
  function needsToken() { return shareState.provider !== "allowlist"; }

  // Búsqueda con rebote: pide un token DELEGADO (`dwHumanCapitalDirectoryToken`) y consulta el directorio. 503 = bloqueado por permiso; se muestra el aviso y se deshabilita.
  function runSearch(doc, win) {
    var input = shareField(doc, "data-share-search");
    if (!input) return;
    var q = (input.value || "").trim();
    var hint = shareField(doc, "data-share-results-hint");
    if (q.length < MIN_QUERY) { renderResults(doc, []); if (hint) { hint.textContent = "Escribí al menos " + MIN_QUERY + " caracteres para buscar."; hint.hidden = false; } showResults(doc, true); return; }
    if (shareState.provider === "allowlist") {                                                  // filtro en memoria: sin consultas por tecla
      if (shareState.catalog === null) { renderResults(doc, []); if (hint) { hint.textContent = "Cargando destinatarios…"; hint.hidden = false; } showResults(doc, true); return; }
      renderResults(doc, catalogMatches(q)); showResults(doc, true); return;
    }
    if (hint) { hint.textContent = "Buscando…"; hint.hidden = false; }
    showResults(doc, true);
    var seq = ++shareState._searchSeq;
    directoryToken(win).then(function (token) {
      if (seq !== shareState._searchSeq) return;                                                  // llegó tarde: hay una búsqueda más nueva
      if (!token && needsToken()) { blockDirectory(doc, "No se pudo obtener autorización de Microsoft para buscar en el directorio."); return; }
      var body = "q=" + encodeURIComponent(q) + "&graph_token=" + encodeURIComponent(token);
      return win.fetch(DIRECTORY_URL, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body })
        .then(function (res) { return res.json().then(function (data) { return { res: res, data: data }; }, function () { return { res: res, data: {} }; }); })
        .then(function (r) {
          if (seq !== shareState._searchSeq) return;
          if (r.res.status === 401) { blockDirectory(doc, "Tu sesión de Microsoft venció. Reingresá y reintentá la búsqueda."); return; }
          if (r.res.status === 503 || (r.data && r.data.blocked)) { blockDirectory(doc, (r.data && r.data.message) || "La búsqueda de destinatarios no está habilitada."); return; }
          if (!r.res.ok) { if (hint) { hint.textContent = "No se pudo consultar el directorio. Reintentá."; hint.hidden = false; } return; }
          renderResults(doc, (r.data && r.data.results) || []);
        });
    }).catch(function () { if (seq === shareState._searchSeq && hint) { hint.textContent = "No se pudo consultar el directorio. Reintentá."; hint.hidden = false; } });
  }

  function blockDirectory(doc, message) {
    shareState.directoryEnabled = false;
    var note = shareField(doc, "data-share-dir-blocked"), input = shareField(doc, "data-share-search");
    if (note) { note.textContent = message || "La búsqueda de destinatarios no está habilitada."; note.hidden = false; }
    if (input) input.disabled = true;
    showResults(doc, false);
    refreshSend(doc);
  }

  // Habilita «Enviar» solo con: proveedor de destinatarios disponible, mensaje listo (CVs elegidos) y ≥1 destinatario; nunca mientras hay un envío en curso o ya se envió.
  function sendReady() {
    var model = resolveShare(shareState.items, shareState.choice);
    return shareState.directoryEnabled && !shareState.sending && !shareState.sent && shareReady(model) && shareState.recipients.length > 0;
  }

  function refreshSend(doc) {
    var btn = shareField(doc, "data-share-send");
    if (!btn) return;
    btn.disabled = !sendReady();
    btn.textContent = shareState.sending ? "Enviando…" : "Enviar";                                // estado de procesamiento en el mismo botón
    if (shareState.sending) btn.setAttribute("aria-busy", "true"); else btn.removeAttribute("aria-busy");
  }

  // Resultado aceptado (Graph 202): se cierra el diálogo, se limpia su estado y el aviso va al toast transitorio de la página (patrón CCHH compartido).
  function acceptedText(n) { return "Correo aceptado para envío a " + n + (n === 1 ? " destinatario." : " destinatarios."); }
  function pageToast(doc, text) {
    var host = doc.querySelector ? doc.querySelector("[data-hc-share-toast]") : null;
    if (!host) return;
    var box = doc.createElement("aside");
    box.className = "callout callout--success callout--feedback";
    box.setAttribute("role", "status");
    box.textContent = text;
    if (host.classList) host.classList.remove("is-dismissing");
    if (host.replaceChildren) host.replaceChildren(box); else { host.textContent = ""; host.appendChild(box); }
  }
  function closeAfterAccepted(doc, n) {
    var dialog = doc.getElementById("hc-share-dialog"), content = doc.getElementById("hc-share-content");
    if (dialog && dialog.open && dialog.close) dialog.close();
    if (content) content.textContent = "";
    shareState = { items: [], choice: {}, recipients: [], directoryEnabled: false, provider: "directory", catalog: null, greeting: null,
                   maxRecipients: 20, operationId: "", sending: false, sent: false, _searchSeq: 0 };
    pageToast(doc, acceptedText(n));
  }

  // «Enviar» envía directamente (sin confirmación secundaria): el usuario ya eligió perfiles, destinatarios y contenido; el servidor revalida todo.
  // El botón se deshabilita en el acto (doble clic = un solo request) y el backend es idempotente por `operation_id`.
  function doSend(doc, win) {
    if (!sendReady()) return;
    shareState.sending = true;
    refreshSend(doc);
    sendStatus(doc, "", false);
    var model = resolveShare(shareState.items, shareState.choice);
    directoryToken(win).then(function (token) {
      if (!token && needsToken()) { shareState.sending = false; sendStatus(doc, "No se pudo obtener autorización de Microsoft para validar los destinatarios.", true); refreshSend(doc); return; }
      var parts = ["operation_id=" + encodeURIComponent(shareState.operationId),
                   "subject=" + encodeURIComponent(fieldValue(doc, "data-share-subject")),
                   "message=" + encodeURIComponent(fieldValue(doc, "data-share-intro")),           // cuerpo del usuario (saludo + texto): los candidatos NO van acá
                   "closing=" + encodeURIComponent(fieldValue(doc, "data-share-outro")),          // cierre del usuario (va DESPUÉS de la lista en el correo)
                   "choice=" + encodeURIComponent(JSON.stringify(shareState.choice || {})),
                   "graph_token=" + encodeURIComponent(token)];
      shareState.items.forEach(function (it) { parts.push("profile_ids=" + encodeURIComponent(it.id)); });
      shareState.recipients.forEach(function (r) { parts.push("recipients=" + encodeURIComponent(r.email)); });
      return win.fetch(SEND_URL, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: parts.join("&") })
        .then(function (res) { return res.json().then(function (data) { return { res: res, data: data }; }, function () { return { res: res, data: {} }; }); })
        .then(function (r) { applySendOutcome(doc, win, r.res, r.data || {}); });
    }).catch(function () { shareState.sending = false; sendStatus(doc, "No se pudo completar el envío. Reintentá en unos instantes.", true); refreshSend(doc); });
  }

  function applySendOutcome(doc, win, res, data) {
    shareState.sending = false;
    var st = data.status;
    if (st === "accepted") { closeAfterAccepted(doc, (data.recipients || shareState.recipients).length); return; }
    else if (st === "duplicate") { shareState.sent = true; sendStatus(doc, "Este envío ya se había registrado; no se reenvió.", true); }
    else if (st === "uncertain") { shareState.sent = true; sendStatus(doc, "El envío quedó sin confirmación de Microsoft. No se reenvía automáticamente: verificá tu bandeja antes de reintentar.", true); }
    else if (st === "config_incomplete" || (data.reason === "audit_unavailable")) { sendStatus(doc, data.message || "No se pudo enviar por un problema de configuración. Nada fue enviado.", true); }      // nada enviado: se puede reintentar con el mismo operation_id
    else if (st === "error") { sendStatus(doc, data.message || "No se pudo enviar. Revisá la selección y los destinatarios.", true); }                                                                          // validación previa: reintentable
    else { shareState.sent = true; sendStatus(doc, data.message || "El servicio de correo rechazó el envío.", true); }                                                                                          // failed u otro: se bloquea para no reintentar a ciegas
    refreshSend(doc);
  }

  // Copia el mensaje con DOS representaciones (text/html con los nombres como hipervínculos y text/plain con las URLs completas) si el navegador lo permite; si no, copia la selección renderizada
  // (también conserva el formato) y, como último recurso, solo el texto plano. `done(modo)`: "rich" | "plain".
  function copyRich(win, doc, html, plain, done, fail) {
    var nav = win.navigator;
    function asPlain() { copyText(win, doc, plain, function () { done("plain"); }, fail); }
    function viaSelection() {
      try {
        var holder = doc.createElement("div");
        holder.innerHTML = html;                                                                 // `html` lo armó `buildHtml`: todo valor ya viene escapado
        holder.style.position = "fixed"; holder.style.opacity = "0"; holder.style.pointerEvents = "none";
        doc.body.appendChild(holder);
        var range = doc.createRange(); range.selectNodeContents(holder);
        var sel = win.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        var ok = doc.execCommand && doc.execCommand("copy");
        sel.removeAllRanges(); doc.body.removeChild(holder);
        if (ok) done("rich"); else asPlain();
      } catch (e) { asPlain(); }
    }
    if (nav && nav.clipboard && nav.clipboard.write && typeof win.ClipboardItem === "function" && typeof win.Blob === "function") {
      try {
        var item = new win.ClipboardItem({ "text/html": new win.Blob([html], { type: "text/html" }), "text/plain": new win.Blob([plain], { type: "text/plain" }) });
        nav.clipboard.write([item]).then(function () { done("rich"); }, viaSelection);
        return;
      } catch (e) { /* cae al método siguiente */ }
    }
    viaSelection();
  }

  function copyText(win, doc, text, done, fail) {
    var nav = win.navigator;
    if (nav && nav.clipboard && nav.clipboard.writeText) { nav.clipboard.writeText(text).then(done, fail); return; }
    try {
      var ta = doc.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      doc.body.appendChild(ta); ta.select();
      var ok = doc.execCommand && doc.execCommand("copy");
      doc.body.removeChild(ta);
      if (ok) done(); else fail();
    } catch (e) { fail(); }
  }

  function openShare(doc, win, store) {
    var content = doc.getElementById("hc-share-content"), dialog = doc.getElementById("hc-share-dialog");
    if (!content || !dialog || store.size() === 0 || store.size() > MAX_SHARE) return;
    function show(html) { content.innerHTML = html; if (dialog.showModal && !dialog.open) dialog.showModal(); }
    function failure(text) {
      show('<div class="dw-confirm__body"><h2 class="dw-confirm__title" id="hc-share-title">Compartir perfiles</h2><p class="dw-confirm__msg"></p></div>' +
           '<div class="dw-confirm__actions"><form method="dialog"><button type="submit" class="btn-secondary">Cerrar</button></form></div>');
      var p = content.querySelector(".dw-confirm__msg"); if (p) p.textContent = text;
    }
    var body = store.ids().map(function (id) { return "profile_ids=" + encodeURIComponent(id); }).join("&");
    win.fetch(SHARE_URL, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body })
      .then(function (res) {
        if (res.redirected || res.status === 401) throw new Error("session");
        if (!res.ok) throw new Error("http");
        return res.text();
      })
      .then(function (html) { show(html); initShare(doc, store, win); })
      .catch(function (err) {
        failure(err && err.message === "session" ? "Tu sesión venció. Volvé a ingresar y repetí la acción." : "No se pudo preparar el mensaje. Reintentá en unos instantes.");
      });
  }

  // «Consolidar perfiles»: POST de SOLO los dos ids (no viajan por la URL) a la pantalla de comparación. El servidor valida permiso y cantidad.
  function openConsolidation(doc, store) {
    if (store.size() !== 2) return;
    var form = doc.createElement("form");
    form.method = "post";
    form.action = "/cchh/candidatos/consolidar";
    store.ids().forEach(function (id) {
      var input = doc.createElement("input");
      input.type = "hidden"; input.name = "profile_ids"; input.value = id;
      form.appendChild(input);
    });
    doc.body.appendChild(form);
    form.submit();
  }

  // ───────────────────────── cableado (delegación en document) ─────────────────────────
  function wire(doc, win, store) {
    doc.addEventListener("change", function (event) {
      var t = event.target;
      if (!t || !t.matches) return;
      if (t.matches("[data-share-choose]")) {
        shareState.choice[t.getAttribute("data-share-choose")] = t.value;
        refreshShare(doc, t.getAttribute("data-share-choose"));
        return;
      }
      if (t.matches("[data-hc-select]")) {
        var id = t.getAttribute("data-hc-select");
        keepInPlace(doc, t, function () { if (t.checked) store.add(id); else store.remove(id); syncUI(doc, store); });
      } else if (t.matches("[data-hc-select-page]")) {
        var visible = [].slice.call(doc.querySelectorAll("[data-hc-select]")).map(function (b) { return b.getAttribute("data-hc-select"); });
        keepInPlace(doc, t, function () { if (t.checked) store.add(visible); else store.remove(visible); syncUI(doc, store); });     // SOLO la página visible
      }
    });
    var searchTimer = null;
    doc.addEventListener("input", function (event) {
      var t = event.target;
      if (t && t.matches && t.matches("[data-share-subject], [data-share-intro], [data-share-outro]")) { if (!t.matches("[data-share-subject]")) autosize(t); refreshCopy(doc); }
      else if (t && t.matches && t.matches("[data-share-search]")) {
        if (!shareState.directoryEnabled) return;
        if (searchTimer) win.clearTimeout(searchTimer);
        searchTimer = win.setTimeout(function () { runSearch(doc, win); }, SEARCH_DEBOUNCE);
      }
    });
    doc.addEventListener("focusin", function (event) {
      var t = event.target;
      if (t && t.matches && t.matches("[data-share-search]") && shareState.directoryEnabled && (t.value || "").trim().length >= MIN_QUERY) runSearch(doc, win);
    });
    doc.addEventListener("click", function (event) {
      var t = event.target, el = t && t.closest ? t.closest("[data-hc-clear-selection], [data-hc-share-open], [data-hc-consolidate-open], [data-share-copy], [data-share-pick], [data-share-chip-remove], [data-share-send]") : null;
      if (!el) {
        var results = shareField(doc, "data-share-results");                                        // clic fuera del selector: cierra el desplegable
        if (results && !results.hidden && !(t && t.closest && t.closest("[data-share-recipients]"))) showResults(doc, false);
        return;
      }
      if (el.hasAttribute("data-share-pick")) {
        addRecipient(doc, { email: el.getAttribute("data-share-pick"), name: el.getAttribute("data-share-pick-name") });
        var search = shareField(doc, "data-share-search");
        if (search) { search.value = ""; search.focus(); }
        renderResults(doc, []); showResults(doc, false);
        return;
      }
      if (el.hasAttribute("data-share-chip-remove")) { removeRecipient(doc, el.getAttribute("data-share-chip-remove")); return; }
      if (el.hasAttribute("data-share-send")) { doSend(doc, win); return; }
      if (el.hasAttribute("data-hc-consolidate-open")) { openConsolidation(doc, store); return; }
      if (el.hasAttribute("data-hc-clear-selection")) { store.clear(); syncUI(doc, store); return; }
      if (el.hasAttribute("data-hc-share-open")) { openShare(doc, win, store); return; }
      if (el.hasAttribute("data-share-copy")) {
        var what = el.getAttribute("data-share-copy"), failed = function () { status(doc, "No se pudo copiar: seleccioná el texto y copialo a mano."); };
        var model = resolveShare(shareState.items, shareState.choice);
        if (!shareReady(model)) return;                                                          // sin CVs elegidos no se copia un mensaje incompleto
        var intro = fieldValue(doc, "data-share-intro"), outro = fieldValue(doc, "data-share-outro");
        copyRich(win, doc, buildHtml(model, intro, outro), buildPlain(model, intro, outro), function (mode) {
          status(doc, mode === "rich" ? "Copiado: al pegarlo en Outlook los nombres quedan como enlaces al CV." : "Copiado como texto plano con las URLs completas (este navegador no permite copiar con formato).");
        }, failed);
        return;
      }
    });
    function resync() { syncUI(doc, store); }
    doc.body.addEventListener("htmx:afterSwap", function (event) {
      var target = event.detail && event.detail.target;
      if (target && target.id === "hc-profiles-panel") resync();
    });
    if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", resync); else resync();
  }

  var api = { STORAGE_KEY: STORAGE_KEY, MAX_SHARE: MAX_SHARE, SKIP: SKIP, createStore: createStore, escapeHtml: escapeHtml, safeCvUrl: safeCvUrl, rowsModel: rowsModel, resolveShare: resolveShare, autosize: autosize,
              buildPlain: buildPlain, buildHtml: buildHtml, copyRich: copyRich, refreshShare: refreshShare, renderList: renderList,
              syncUI: syncUI, keepInPlace: keepInPlace, refreshCopy: refreshCopy, greetingFor: greetingFor, applyGreeting: applyGreeting, initShare: initShare, openShare: openShare, wire: wire,
              MIN_QUERY: MIN_QUERY, DIRECTORY_URL: DIRECTORY_URL, SEND_URL: SEND_URL, CATALOG_URL: CATALOG_URL, loadCatalog: loadCatalog, catalogMatches: catalogMatches, newOperationId: newOperationId, renderChips: renderChips, addRecipient: addRecipient,
              removeRecipient: removeRecipient, renderResults: renderResults, runSearch: runSearch, blockDirectory: blockDirectory, sendReady: sendReady, refreshSend: refreshSend,
              doSend: doSend, applySendOutcome: applySendOutcome, acceptedText: acceptedText,
              _state: function () { return shareState; } };
  if (typeof window !== "undefined") {
    window.__cchhSelection = api;
    if (typeof document !== "undefined" && document.body && !window.__cchhSelectionWired) {
      var storage = null;
      try { storage = window.sessionStorage; } catch (e) { storage = null; }
      window.__cchhSelectionWired = true;
      var activeStore = createStore(storage);
      api.store = activeStore;                                                              // lo usa `human_capital_candidates_download.js` (solo lectura de los ids)
      wire(document, window, activeStore);
    }
  }
})();

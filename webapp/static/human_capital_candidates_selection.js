/* human_capital_candidates_selection.js — CCHH · Candidatos: selección múltiple + «Compartir perfiles».

   QUÉ HACE
   · Mantiene un conjunto de `profile_id` (UUID opacos, NADA más: ni nombres ni datos personales) en `sessionStorage` (por pestaña, muere con la sesión del navegador; jamás
     localStorage). Persiste entre páginas y filtros: el panel del listado se reemplaza entero en cada filtro/orden/página (HTMX), así que el estado vive AFUERA del fragmento y
     después de cada swap se re-marcan las casillas visibles.
   · Casilla de encabezado: selecciona/deselecciona SOLO las filas de la página visible; es tri-estado (marcada / indeterminada / vacía).
   · Barra contextual (oculta con 0 seleccionados; el desplazamiento al aparecer/desaparecer se compensa para no desviar el clic siguiente): contador, «Compartir perfiles», «Descargar CVs» y «✕ Limpiar» (el mismo componente que el de los filtros).
   · «Compartir perfiles»: pide al servidor (POST, solo ids) el contenido del diálogo —nombres y rutas de las FICHAS resueltos en servidor— y arma el mensaje con el origen del
     propio navegador. «Copiar» y «Abrir Outlook» (`mailto:`) son acciones explícitas del usuario; no se envía ni se registra nada.

   LÍMITES
   · Compartir: hasta MAX_SHARE perfiles (el servidor valida igual: esta cota es solo UX).
   · `mailto:` tiene un tope práctico de longitud (~2 000 caracteres en Windows/Outlook): pasado MAILTO_MAX se deshabilita «Abrir Outlook» con una nota y queda «Copiar».

   Sin dependencias; se carga una sola vez desde `human_capital/profiles.html` y delega eventos en `document` (sobrevive a los swaps). Expone `window.__cchhSelection` para pruebas. */
(function () {
  "use strict";

  var STORAGE_KEY = "cchh-candidatos-selection";
  var STORAGE_VERSION = 1;
  var ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
  var MAX_STORED = 2000;
  var MAX_SHARE = 25;
  var MAILTO_MAX = 1900;
  var SHARE_URL = "/cchh/candidatos/compartir";

  // ───────────────────────── almacén de la selección (solo ids) ─────────────────────────
  function createStore(storage) {
    var ids = [];
    var known = Object.create(null);

    function persist() {
      if (!storage) return;
      try { storage.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, ids: ids })); } catch (e) { /* modo privado/cuota: sigue en memoria, no rompe */ }
    }

    (function load() {
      if (!storage) return;
      try {
        var raw = storage.getItem(STORAGE_KEY);
        if (!raw) return;
        var parsed = JSON.parse(raw);
        if (!parsed || parsed.v !== STORAGE_VERSION || !Array.isArray(parsed.ids)) return;     // forma desconocida: se descarta entera (fail-closed)
        parsed.ids.forEach(function (id) {
          if (typeof id === "string" && ID_RE.test(id) && !known[id] && ids.length < MAX_STORED) { known[id] = true; ids.push(id); }
        });
      } catch (e) { ids = []; known = Object.create(null); }
    })();

    return {
      ids: function () { return ids.slice(); },
      has: function (id) { return !!known[id]; },
      size: function () { return ids.length; },
      add: function (list) {
        var changed = false;
        [].concat(list).forEach(function (id) {
          if (typeof id === "string" && ID_RE.test(id) && !known[id] && ids.length < MAX_STORED) { known[id] = true; ids.push(id); changed = true; }
        });
        if (changed) persist();
        return changed;
      },
      remove: function (list) {
        var drop = Object.create(null);
        [].concat(list).forEach(function (id) { drop[id] = true; });
        var before = ids.length;
        ids = ids.filter(function (id) { if (drop[id]) { delete known[id]; return false; } return true; });
        if (ids.length !== before) persist();
        return ids.length !== before;
      },
      clear: function () { ids = []; known = Object.create(null); persist(); }
    };
  }

  // ───────────────────────── mensaje (HTML + texto plano) y mailto ─────────────────────────
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

  // Texto plano (fallback del portapapeles y cuerpo del `mailto:`): nombre y URL COMPLETA de cada CV.
  function buildPlain(model, intro, outro) {
    var lines = model.included.map(function (n) { return "- " + n.name + ": " + n.url; });
    return [String(intro || "")].concat([""], lines, [""], [String(outro || "")]).join("\n");
  }

  // HTML (portapapeles `text/html`): el nombre es el hipervínculo al CV; sin URLs largas a la vista. Todo valor va escapado (nombres y atributos).
  function buildHtml(model, intro, outro) {
    var list = model.included.map(function (n) { return '<li><a href="' + escapeHtml(n.url) + '">' + escapeHtml(n.name) + "</a></li>"; }).join("");
    return (intro ? paragraph(intro) : "") + "<ul>" + list + "</ul>" + (outro ? paragraph(outro) : "");
  }

  // `:` y `/` son válidos sin codificar en un mailto (RFC 6068); codificarlos duplicaba el largo de cada enlace y acortaba de más el límite práctico.
  function encodeMail(text) { return encodeURIComponent(text).replace(/%3A/g, ":").replace(/%2F/g, "/"); }

  // El cuerpo de un `mailto:` es texto SIN formato (RFC 6068): no admite hipervínculos con nombre. «Abrir Outlook» usa el texto plano con las URLs completas.
  function mailtoUrl(subject, body) {
    return "mailto:?subject=" + encodeMail(subject || "") + "&body=" + encodeMail(String(body || "").replace(/\r?\n/g, "\r\n"));
  }

  function mailtoFits(subject, body) { return mailtoUrl(subject, body).length <= MAILTO_MAX; }

  // ───────────────────────── sincronización de la interfaz ─────────────────────────
  // Al aparecer/desaparecer la barra, todo lo que está debajo (la tabla) se corre su alto: el próximo clic caería en otra fila. `keepInPlace` mide dónde estaba el control accionado, aplica el
  // cambio y reubica el desplazamiento del contenedor para que ese control quede exactamente donde estaba (si no hay nada que compensar, no hace nada).
  function scroller(doc, el) {
    var win = doc.defaultView;
    for (var node = el && el.parentElement; node && win && win.getComputedStyle; node = node.parentElement) {
      var oy = win.getComputedStyle(node).overflowY;
      if ((oy === "auto" || oy === "scroll") && node.scrollHeight > node.clientHeight) return node;
    }
    return doc.scrollingElement || doc.documentElement || null;
  }

  function keepInPlace(doc, anchor, change) {
    var canMeasure = anchor && anchor.getBoundingClientRect;
    var before = canMeasure ? anchor.getBoundingClientRect().top : 0;
    change();
    if (!canMeasure || !anchor.isConnected) return;
    var delta = anchor.getBoundingClientRect().top - before;
    var box = delta ? scroller(doc, anchor) : null;
    if (box) box.scrollTop += delta;
  }

  function syncUI(doc, store) {
    var boxes = [].slice.call(doc.querySelectorAll("[data-hc-select]"));
    var visibleSelected = 0;
    boxes.forEach(function (box) {
      var on = store.has(box.getAttribute("data-hc-select"));
      box.checked = on;
      if (on) visibleSelected++;
      var row = box.closest ? box.closest("tr") : null;
      if (row && row.classList) { if (on) row.classList.add("is-selected"); else row.classList.remove("is-selected"); }
    });
    [].slice.call(doc.querySelectorAll("[data-hc-select-page]")).forEach(function (head) {
      head.disabled = boxes.length === 0;
      head.checked = boxes.length > 0 && visibleSelected === boxes.length;
      head.indeterminate = visibleSelected > 0 && visibleSelected < boxes.length;
    });
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
  var shareState = { items: [], choice: {} };

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

  // Enlace «Abrir Outlook»: texto plano con URLs completas; se deshabilita si no está listo o si el `mailto:` excede el límite práctico (entonces «Copiar mensaje» es el camino).
  function refreshMailto(doc) {
    var model = resolveShare(shareState.items, shareState.choice);
    var ready = shareReady(model);
    var link = shareField(doc, "data-share-open"), note = shareField(doc, "data-share-note"), copy = shareField(doc, 'data-share-copy="mensaje"');
    if (copy) copy.disabled = !ready;
    if (!link) return;
    var subject = fieldValue(doc, "data-share-subject");
    var plain = buildPlain(model, fieldValue(doc, "data-share-intro"), fieldValue(doc, "data-share-outro"));
    var fits = ready && mailtoFits(subject, plain);
    if (fits) {
      link.setAttribute("href", mailtoUrl(subject, plain));
      link.removeAttribute("aria-disabled");
      if (link.classList) link.classList.remove("is-disabled");
    } else {
      link.setAttribute("href", "#");
      link.setAttribute("aria-disabled", "true");
      if (link.classList) link.classList.add("is-disabled");
    }
    setNote(note, ready && !fits ? "El mensaje es demasiado largo para abrirlo en Outlook (límite práctico de los enlaces de correo, que solo admiten texto plano con las URLs completas): usá «Copiar mensaje» y pegalo en tu correo." : "");
  }

  function refreshShare(doc, focusId) {
    var model = resolveShare(shareState.items, shareState.choice);
    renderList(doc, rowsModel(shareState.items, shareState.choice), focusId);
    var pending = shareField(doc, "data-share-pending");
    if (model.pending.length) setNote(pending, "Falta elegir el CV de " + model.pending.length + (model.pending.length === 1 ? " candidato" : " candidatos") + " (o «No incluir a este candidato»).");
    else if (model.included.length === 0) setNote(pending, "Ningún candidato tiene un CV con enlace disponible para compartir.");
    else setNote(pending, "");
    refreshMailto(doc);
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
    shareState = { items: items, choice: {} };
    shareField(doc, "data-share-subject").value = root.getAttribute("data-default-subject") || "";
    shareField(doc, "data-share-intro").value = root.getAttribute("data-intro") || "";
    shareField(doc, "data-share-outro").value = root.getAttribute("data-outro") || "";
    autosize(shareField(doc, "data-share-intro"));
    autosize(shareField(doc, "data-share-outro"));
    refreshShare(doc);
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
    doc.addEventListener("input", function (event) {
      var t = event.target;
      if (t && t.matches && t.matches("[data-share-subject], [data-share-intro], [data-share-outro]")) { if (!t.matches("[data-share-subject]")) autosize(t); refreshMailto(doc); }
    });
    doc.addEventListener("click", function (event) {
      var t = event.target, el = t && t.closest ? t.closest("[data-hc-clear-selection], [data-hc-share-open], [data-hc-consolidate-open], [data-share-copy], [data-share-open]") : null;
      if (!el) return;
      if (el.hasAttribute("data-hc-consolidate-open")) { openConsolidation(doc, store); return; }
      if (el.hasAttribute("data-hc-clear-selection")) { store.clear(); syncUI(doc, store); return; }
      if (el.hasAttribute("data-hc-share-open")) { openShare(doc, win, store); return; }
      if (el.hasAttribute("data-share-copy")) {
        var what = el.getAttribute("data-share-copy"), failed = function () { status(doc, "No se pudo copiar: seleccioná el texto y copialo a mano."); };
        if (what === "asunto") {
          var subject = shareField(doc, "data-share-subject");
          if (subject) copyText(win, doc, subject.value, function () { status(doc, "Copiado al portapapeles."); }, failed);
          return;
        }
        var model = resolveShare(shareState.items, shareState.choice);
        if (!shareReady(model)) return;                                                          // sin CVs elegidos no se copia un mensaje incompleto
        var intro = fieldValue(doc, "data-share-intro"), outro = fieldValue(doc, "data-share-outro");
        copyRich(win, doc, buildHtml(model, intro, outro), buildPlain(model, intro, outro), function (mode) {
          status(doc, mode === "rich" ? "Copiado: al pegarlo en Outlook los nombres quedan como enlaces al CV." : "Copiado como texto plano con las URLs completas (este navegador no permite copiar con formato).");
        }, failed);
        return;
      }
      if (el.hasAttribute("data-share-open") && el.getAttribute("aria-disabled") === "true") event.preventDefault();      // Outlook deshabilitado por longitud
    });
    function resync() { syncUI(doc, store); }
    doc.body.addEventListener("htmx:afterSwap", function (event) {
      var target = event.detail && event.detail.target;
      if (target && target.id === "hc-profiles-panel") resync();
    });
    if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", resync); else resync();
  }

  var api = { STORAGE_KEY: STORAGE_KEY, MAX_SHARE: MAX_SHARE, MAILTO_MAX: MAILTO_MAX, SKIP: SKIP, createStore: createStore, escapeHtml: escapeHtml, safeCvUrl: safeCvUrl, rowsModel: rowsModel, resolveShare: resolveShare, autosize: autosize,
              buildPlain: buildPlain, buildHtml: buildHtml, copyRich: copyRich, refreshShare: refreshShare, renderList: renderList, mailtoUrl: mailtoUrl,
              mailtoFits: mailtoFits, syncUI: syncUI, keepInPlace: keepInPlace, refreshMailto: refreshMailto, initShare: initShare, openShare: openShare, wire: wire };
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

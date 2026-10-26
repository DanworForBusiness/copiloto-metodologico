/* human_capital_candidates_selection.js — CCHH · Candidatos: selección múltiple + «Compartir perfiles».

   QUÉ HACE
   · Mantiene un conjunto de `profile_id` (UUID opacos, NADA más: ni nombres ni datos personales) en `sessionStorage` (por pestaña, muere con la sesión del navegador; jamás
     localStorage). Persiste entre páginas y filtros: el panel del listado se reemplaza entero en cada filtro/orden/página (HTMX), así que el estado vive AFUERA del fragmento y
     después de cada swap se re-marcan las casillas visibles.
   · Casilla de encabezado: selecciona/deselecciona SOLO las filas de la página visible; es tri-estado (marcada / indeterminada / vacía).
   · Barra contextual (siempre visible; con 0 seleccionados queda tenue y con las acciones deshabilitadas): contador, «Compartir perfiles» y «Limpiar selección».
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

  // ───────────────────────── mensaje y mailto ─────────────────────────
  function buildMessage(items, origin, intro, outro) {
    // ASCII para el marcador y el separador: cada carácter no ASCII se codifica en 9 caracteres dentro del `mailto:` y el límite práctico de longitud es lo que acota cuántos perfiles entran.
    var lines = (items || []).map(function (item) { return "- " + item.name + ": " + String(origin || "") + item.path; });
    return [String(intro || "")].concat([""], lines, [""], [String(outro || "")]).join("\n");
  }

  // `:` y `/` son válidos sin codificar en un mailto (RFC 6068); codificarlos duplicaba el largo de cada enlace y acortaba de más el límite práctico.
  function encodeMail(text) { return encodeURIComponent(text).replace(/%3A/g, ":").replace(/%2F/g, "/"); }

  function mailtoUrl(subject, body) {
    return "mailto:?subject=" + encodeMail(subject || "") + "&body=" + encodeMail(String(body || "").replace(/\r?\n/g, "\r\n"));
  }

  function mailtoFits(subject, body) { return mailtoUrl(subject, body).length <= MAILTO_MAX; }

  // ───────────────────────── sincronización de la interfaz ─────────────────────────
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
      bar.hidden = false;                                                                   // siempre visible: aparecer al marcar la primera casilla desplazaría la tabla
      if (bar.classList) { if (total === 0) bar.classList.add("is-empty"); else bar.classList.remove("is-empty"); }
      var count = bar.querySelector("[data-hc-selection-count]");
      if (count) count.textContent = String(total);
      var label = bar.querySelector("[data-hc-selection-label]");
      if (label) label.textContent = total === 1 ? "seleccionado" : "seleccionados";
      var hint = bar.querySelector("[data-hc-selection-hint]");
      if (hint) {
        var outside = total - visibleSelected;
        var parts = [];
        if (total === 0) parts.push("Marcá las casillas de la tabla para compartir perfiles.");
        if (outside > 0) parts.push(outside + (outside === 1 ? " fuera de la vista actual (otra página o filtro)" : " fuera de la vista actual (otras páginas o filtros)"));
        if (total > MAX_SHARE) parts.push("Compartir admite hasta " + MAX_SHARE + " perfiles");
        hint.textContent = parts.join(" · ");
      }
      var clear = bar.querySelector("[data-hc-clear-selection]");
      if (clear) clear.disabled = total === 0;
      var share = bar.querySelector("[data-hc-share-open]");
      if (share) {
        share.disabled = total === 0 || total > MAX_SHARE;
        share.setAttribute("title", total > MAX_SHARE ? "Se pueden compartir hasta " + MAX_SHARE + " perfiles por vez" : "");
      }
    });
  }

  // ───────────────────────── diálogo de compartir ─────────────────────────
  function shareRoot(doc) { var c = doc.getElementById("hc-share-content"); return c && c.querySelector("[data-share-root]"); }
  // Los campos se buscan en TODO el contenedor del diálogo (no solo en `data-share-root`): las acciones —«Copiar mensaje», «Abrir Outlook»— y los JSON de datos viven fuera del bloque `root`.
  function shareField(doc, name) { var c = doc.getElementById("hc-share-content"); return c && shareRoot(doc) ? c.querySelector("[" + name + "]") : null; }

  function refreshMailto(doc) {
    var subject = shareField(doc, "data-share-subject"), message = shareField(doc, "data-share-message");
    var link = shareField(doc, "data-share-open"), note = shareField(doc, "data-share-note");
    if (!subject || !message || !link) return;
    var fits = mailtoFits(subject.value, message.value);
    if (fits) {
      link.setAttribute("href", mailtoUrl(subject.value, message.value));
      link.removeAttribute("aria-disabled");
      if (link.classList) link.classList.remove("is-disabled");
    } else {
      link.setAttribute("href", "#");
      link.setAttribute("aria-disabled", "true");
      if (link.classList) link.classList.add("is-disabled");
    }
    if (note) {
      note.hidden = fits;
      note.textContent = fits ? "" : "El mensaje es demasiado largo para abrirlo en Outlook (límite práctico de los enlaces de correo): usá «Copiar mensaje» y pegalo en tu correo.";
    }
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
    var origin = (win && win.location && win.location.origin) || "";
    shareField(doc, "data-share-subject").value = root.getAttribute("data-default-subject") || "";
    shareField(doc, "data-share-message").value = buildMessage(items, origin, root.getAttribute("data-intro"), root.getAttribute("data-outro"));
    refreshMailto(doc);
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

  // ───────────────────────── cableado (delegación en document) ─────────────────────────
  function wire(doc, win, store) {
    doc.addEventListener("change", function (event) {
      var t = event.target;
      if (!t || !t.matches) return;
      if (t.matches("[data-hc-select]")) {
        var id = t.getAttribute("data-hc-select");
        if (t.checked) store.add(id); else store.remove(id);
        syncUI(doc, store);
      } else if (t.matches("[data-hc-select-page]")) {
        var visible = [].slice.call(doc.querySelectorAll("[data-hc-select]")).map(function (b) { return b.getAttribute("data-hc-select"); });
        if (t.checked) store.add(visible); else store.remove(visible);                              // SOLO la página visible
        syncUI(doc, store);
      }
    });
    doc.addEventListener("input", function (event) {
      var t = event.target;
      if (t && t.matches && t.matches("[data-share-subject], [data-share-message]")) refreshMailto(doc);
    });
    doc.addEventListener("click", function (event) {
      var t = event.target, el = t && t.closest ? t.closest("[data-hc-clear-selection], [data-hc-share-open], [data-share-copy], [data-share-open]") : null;
      if (!el) return;
      if (el.hasAttribute("data-hc-clear-selection")) { store.clear(); syncUI(doc, store); return; }
      if (el.hasAttribute("data-hc-share-open")) { openShare(doc, win, store); return; }
      if (el.hasAttribute("data-share-copy")) {
        var what = el.getAttribute("data-share-copy"), source = shareField(doc, what === "asunto" ? "data-share-subject" : "data-share-message");
        if (source) copyText(win, doc, source.value, function () { status(doc, "Copiado al portapapeles."); }, function () { status(doc, "No se pudo copiar: seleccioná el texto y copialo a mano."); });
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

  var api = { STORAGE_KEY: STORAGE_KEY, MAX_SHARE: MAX_SHARE, MAILTO_MAX: MAILTO_MAX, createStore: createStore, buildMessage: buildMessage, mailtoUrl: mailtoUrl,
              mailtoFits: mailtoFits, syncUI: syncUI, refreshMailto: refreshMailto, initShare: initShare, openShare: openShare, wire: wire };
  if (typeof window !== "undefined") {
    window.__cchhSelection = api;
    if (typeof document !== "undefined" && document.body && !window.__cchhSelectionWired) {
      var storage = null;
      try { storage = window.sessionStorage; } catch (e) { storage = null; }
      window.__cchhSelectionWired = true;
      wire(document, window, createStore(storage));
    }
  }
})();

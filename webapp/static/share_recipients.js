/* share_recipients.js — Selector de destinatarios CORPORATIVOS, TRANSVERSAL a las verticales del Hub (Compartir perfiles de Capital Humano,
   Compartir de Comercial/CRM, …). Extraído de `human_capital_candidates_selection.js` sin cambiar su comportamiento: búsqueda desde 3 caracteres
   (nombre, apellido, alias, correo; sin mayúsculas ni acentos), catálogo UNA vez por apertura y filtro en memoria (proveedor «allowlist») o
   directorio en vivo (proveedor «directory»), chips con selección múltiple, saludo personalizado «Hola <Nombre>,». Sin caché persistente.

   Cada consumidor aporta un HOST (adaptador): `state()` → estado vivo del diálogo ({recipients, provider, catalog, directoryEnabled,
   maxRecipients, greeting, _searchSeq}); `field(doc, attr)` → elemento del diálogo por atributo `data-*`; `refresh(doc)` → recalcular acciones;
   `status(doc, text, warn)` → aviso; `urls: {catalog, directory}` → rutas del SERVIDOR de esa vertical (la vertical la decide el backend);
   opcionales `introAttr` (campo de saludo) y `directoryTokenFn` (nombre de la función de token delegado del directorio).
   Los atributos de marcado son los del diálogo de CCHH (`data-share-*`): un consumidor nuevo usa el MISMO marcado. */
(function () {
  "use strict";
  var MIN_QUERY = 3;

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
  function applyGreeting(h, doc) {
    var intro = h.field(doc, h.introAttr || "data-share-intro");
    if (!intro || h.state().greeting === null) return;
    if (currentGreeting(intro.value) !== h.state().greeting) { h.state().greeting = null; return; }      // lo editó el usuario: se respeta
    var next = greetingFor(h.state().recipients);
    if (next === h.state().greeting) return;
    intro.value = next + intro.value.slice(h.state().greeting.length);
    h.state().greeting = next;
  }

  // «allowlist»: UNA lectura por apertura (catálogo = HUMAN_CAPITAL_ALLOWED_USERS + nombres del padrón con el token base de lectura). Si el
  // token o SharePoint fallan, el servidor devuelve igual los correos; si la ruta misma falla, la búsqueda informa y se puede reabrir.
  function loadCatalog(h, doc, win) {
    var opened = h.state();
    var tokenFn = win.dwGraphToken;
    return Promise.resolve(tokenFn ? tokenFn() : "").catch(function () { return ""; }).then(function (token) {
      return win.fetch(h.urls.catalog, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" },
                                      body: "graph_token=" + encodeURIComponent(token || "") });
    }).then(function (res) { return res.ok ? res.json() : { recipients: [] }; }).then(function (data) {
      if (h.state() !== opened) return;                                                         // el diálogo se reabrió mientras cargaba
      h.state().catalog = (data && data.recipients) || [];
      var input = h.field(doc, "data-share-search");
      if (input && (input.value || "").trim().length >= MIN_QUERY) runSearch(h, doc, win);
    }).catch(function () { if (h.state() === opened) h.state().catalog = []; });
  }

  function fold(text) { return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }

  // Coincidencia en memoria: nombre, apellido (cualquier palabra del nombre), alias (parte local) o correo; sin distinguir acentos.
  function catalogMatches(h, q) {
    var needle = fold(q);
    return (h.state().catalog || []).filter(function (p) {
      var email = fold(p.email), alias = email.split("@")[0];
      return fold(p.name).indexOf(needle) !== -1 || email.indexOf(needle) !== -1 || alias.indexOf(needle) !== -1;
    });
  }

  function hasRecipient(h, email) {
    var key = String(email || "").toLowerCase();
    return h.state().recipients.some(function (r) { return r.email.toLowerCase() === key; });
  }

  function renderChips(h, doc) {
    var list = h.field(doc, "data-share-chips");
    if (!list) return;
    list.textContent = "";
    h.state().recipients.forEach(function (r) {
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

  function addRecipient(h, doc, person) {
    if (!person || !person.email || hasRecipient(h, person.email)) return;
    if (h.state().recipients.length >= h.state().maxRecipients) { h.status(doc, "Se pueden agregar hasta " + h.state().maxRecipients + " destinatarios.", true); return; }
    h.state().recipients.push({ name: person.name || person.email, email: person.email });
    renderChips(h, doc);
    applyGreeting(h, doc);
    h.refresh(doc);
  }

  function removeRecipient(h, doc, email) {
    var key = String(email || "").toLowerCase();
    h.state().recipients = h.state().recipients.filter(function (r) { return r.email.toLowerCase() !== key; });
    renderChips(h, doc);
    applyGreeting(h, doc);
    h.refresh(doc);
  }

  function showResults(h, doc, show) {
    var box = h.field(doc, "data-share-results"), input = h.field(doc, "data-share-search");
    if (box) box.hidden = !show;
    if (input) input.setAttribute("aria-expanded", show ? "true" : "false");
  }

  function renderResults(h, doc, rows) {
    var listEl = h.field(doc, "data-share-results-list"), hint = h.field(doc, "data-share-results-hint");
    if (!listEl) return;
    listEl.textContent = "";
    rows.forEach(function (p) {
      var opt = doc.createElement("div");
      opt.setAttribute("role", "option");
      opt.setAttribute("data-share-pick", p.email);
      opt.setAttribute("data-share-pick-name", p.name || p.email);
      opt.setAttribute("aria-selected", hasRecipient(h, p.email) ? "true" : "false");
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
  function directoryToken(h, win) {
    if (h.state().provider === "allowlist") return Promise.resolve("");
    var tokenFn = win[h.directoryTokenFn || "dwHumanCapitalDirectoryToken"];
    return Promise.resolve(tokenFn ? tokenFn() : "");
  }
  function needsToken(h) { return h.state().provider !== "allowlist"; }

  // Búsqueda con rebote: pide un token DELEGADO (`dwHumanCapitalDirectoryToken`) y consulta el directorio. 503 = bloqueado por permiso; se muestra el aviso y se deshabilita.
  function runSearch(h, doc, win) {
    var input = h.field(doc, "data-share-search");
    if (!input) return;
    var q = (input.value || "").trim();
    var hint = h.field(doc, "data-share-results-hint");
    if (q.length < MIN_QUERY) { renderResults(h, doc, []); if (hint) { hint.textContent = "Escribí al menos " + MIN_QUERY + " caracteres para buscar."; hint.hidden = false; } showResults(h, doc, true); return; }
    if (h.state().provider === "allowlist") {                                                  // filtro en memoria: sin consultas por tecla
      if (h.state().catalog === null) { renderResults(h, doc, []); if (hint) { hint.textContent = "Cargando destinatarios…"; hint.hidden = false; } showResults(h, doc, true); return; }
      renderResults(h, doc, catalogMatches(h, q)); showResults(h, doc, true); return;
    }
    if (hint) { hint.textContent = "Buscando…"; hint.hidden = false; }
    showResults(h, doc, true);
    var seq = ++h.state()._searchSeq;
    directoryToken(h, win).then(function (token) {
      if (seq !== h.state()._searchSeq) return;                                                  // llegó tarde: hay una búsqueda más nueva
      if (!token && needsToken(h)) { blockDirectory(h, doc, "No se pudo obtener autorización de Microsoft para buscar en el directorio."); return; }
      var body = "q=" + encodeURIComponent(q) + "&graph_token=" + encodeURIComponent(token);
      return win.fetch(h.urls.directory, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body })
        .then(function (res) { return res.json().then(function (data) { return { res: res, data: data }; }, function () { return { res: res, data: {} }; }); })
        .then(function (r) {
          if (seq !== h.state()._searchSeq) return;
          if (r.res.status === 401) { blockDirectory(h, doc, "Tu sesión de Microsoft venció. Reingresá y reintentá la búsqueda."); return; }
          if (r.res.status === 503 || (r.data && r.data.blocked)) { blockDirectory(h, doc, (r.data && r.data.message) || "La búsqueda de destinatarios no está habilitada."); return; }
          if (!r.res.ok) { if (hint) { hint.textContent = "No se pudo consultar el directorio. Reintentá."; hint.hidden = false; } return; }
          renderResults(h, doc, (r.data && r.data.results) || []);
        });
    }).catch(function () { if (seq === h.state()._searchSeq && hint) { hint.textContent = "No se pudo consultar el directorio. Reintentá."; hint.hidden = false; } });
  }

  function blockDirectory(h, doc, message) {
    h.state().directoryEnabled = false;
    var note = h.field(doc, "data-share-dir-blocked"), input = h.field(doc, "data-share-search");
    if (note) { note.textContent = message || "La búsqueda de destinatarios no está habilitada."; note.hidden = false; }
    if (input) input.disabled = true;
    showResults(h, doc, false);
    h.refresh(doc);
  }

  var api = { MIN_QUERY: MIN_QUERY, firstName: firstName, greetingFor: greetingFor, currentGreeting: currentGreeting, applyGreeting: applyGreeting,
              loadCatalog: loadCatalog, fold: fold, catalogMatches: catalogMatches, hasRecipient: hasRecipient, renderChips: renderChips,
              addRecipient: addRecipient, removeRecipient: removeRecipient, showResults: showResults, renderResults: renderResults,
              directoryToken: directoryToken, needsToken: needsToken, runSearch: runSearch, blockDirectory: blockDirectory };
  if (typeof window !== "undefined") window.dwShareRecipients = api;
})();

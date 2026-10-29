/* commercial_presentations.js — Comercial → Presentaciones (V1): «Copiar enlace», «Copiar como nueva» y guardado del editor.
   Sin estado de negocio en el navegador: la autoridad es SIEMPRE el servidor (sesión, Comercial, propiedad, revisión, validación). Los valores se escriben con `textContent`/`value`, nunca con innerHTML. */
(function () {
  "use strict";

  // Confirmación TRANSITORIA: el contenedor `[data-cp-status]` lleva `data-dw-auto-dismiss` (primitivo Hub-wide, app.js `initAutoDismissFeedback`, el mismo del refresh de catálogos de CCHH):
  // se vacía solo por timeout o al click afuera, y cada contenido nuevo reinicia el temporizador. Acá solo se arma el MISMO markup `.callout` (cabecera + cuerpo) con `textContent` y se
  // REEMPLAZA lo anterior (`replaceChildren`): varias acciones seguidas muestran un único aviso, nunca una pila. Sin texto, solo limpia.
  function toast(title, text, tone) {
    var host = document.querySelector("[data-cp-status]");
    if (!host) return;
    if (!title && !text) { host.replaceChildren(); return; }
    var box = document.createElement("div");
    box.className = "callout callout--" + (tone || "success");
    box.setAttribute("role", "status");
    var head = document.createElement("div");
    head.className = "callout__head";
    var icon = document.createElement("span");
    icon.className = "callout__icon";
    icon.textContent = tone === "punch" ? "⚠️" : tone === "note" ? "ℹ️" : "✓";
    var heading = document.createElement("span");
    heading.className = "callout__title";
    heading.textContent = title;
    head.appendChild(icon);
    head.appendChild(heading);
    var body = document.createElement("div");
    body.className = "callout__body";
    var line = document.createElement("p");
    line.textContent = text || "";
    body.appendChild(line);
    box.appendChild(head);
    box.appendChild(body);
    host.replaceChildren(box);
  }

  async function readJson(res) {
    try { return await res.json(); } catch (e) { return {}; }
  }

  function messageOf(data, fallback) {
    return (data && (data.message || data.detail)) || fallback;
  }

  // ── Buscar (catálogo): filtro 100% cliente sobre las filas ya renderizadas ────────────────────────────────────
  // Sin endpoint ni preferencias persistidas. Normaliza con el MISMO helper de búsqueda libre del Hub (`dwSearchFold`, app.js: sin mayúsculas ni tildes, espacios recortados y colapsados) y compara contra
  // `data-cp-search` (título + responsable). El contador del agrupador muestra el total con la búsqueda vacía y los coincidentes con búsqueda activa; sin coincidencias se muestra el estado vacío del Hub.
  (function () {
    var input = document.querySelector("[data-cp-search-input]");
    if (!input) return;
    function apply() {
      var q = dwSearchFold(input.value);
      Array.prototype.forEach.call(document.querySelectorAll("details.dashboard-section[data-section-id]"), function (section) {     // un agrupador por colección: mismo filtro, contador propio
        var rows = Array.prototype.slice.call(section.querySelectorAll("tbody tr[data-cp-search]"));                                // se relee: una fila eliminada ya no cuenta
        var shown = 0;
        rows.forEach(function (tr) {
          var hit = !q || dwSearchFold(tr.getAttribute("data-cp-search")).indexOf(q) !== -1;
          tr.hidden = !hit;
          if (hit) shown += 1;
        });
        var count = section.querySelector("[data-cp-count]");
        var wrap = section.querySelector("[data-cp-table-wrap]");
        var empty = section.querySelector("[data-cp-empty]");
        if (count) count.textContent = shown + " " + (shown === 1 ? count.getAttribute("data-cp-singular") : count.getAttribute("data-cp-plural"));
        if (wrap) wrap.hidden = shown === 0;
        if (empty) empty.hidden = shown !== 0;
      });
    }
    input.addEventListener("input", apply);
    input.closest("form").addEventListener("submit", function (ev) { ev.preventDefault(); });
    document.addEventListener("cp:rows-changed", apply);
    // `dwSearchFold` vive en app.js, que base.html carga DESPUÉS de este script: el primer pase espera a que terminen los scripts sincrónicos (DOMContentLoaded). Antes de eso no hay nada que filtrar.
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();
  })();

  // ── Eliminar (catálogo y detalle) ──────────────────────────────────────────────────────────────────────────────
  // Confirmación con el diálogo nativo del Hub (`dwConfirm`, app.js: foco en «Cancelar», Escape/backdrop). La autoridad es el servidor (responsable o APP_OWNER; la semilla nunca). En el catálogo la fila
  // sale del DOM, el contador se recalcula y se avisa con el toast transitorio; en el detalle se vuelve al catálogo con el aviso en `sessionStorage` (solo UI, un uso).
  var FLASH_KEY = "cp-flash";
  try {
    var flash = window.sessionStorage.getItem(FLASH_KEY);
    if (flash) { window.sessionStorage.removeItem(FLASH_KEY); toast("Presentación eliminada", flash, "success"); }
  } catch (e) { /* sin sessionStorage: el aviso se omite, la eliminación ya ocurrió */ }

  document.addEventListener("click", async function (ev) {
    var btn = ev.target.closest("[data-cp-delete]");
    if (!btn || btn.disabled) return;
    var id = btn.getAttribute("data-cp-delete");
    var title = btn.getAttribute("data-cp-title") || "esta presentación";
    var message = "Se eliminará «" + title + "» de forma definitiva. El enlace compartido dejará de funcionar y no se puede deshacer.";
    var confirmed = typeof dwConfirm === "function"
      ? await dwConfirm({ title: "Eliminar presentación", message: message, confirmLabel: "Eliminar" })
      : window.confirm(message);
    if (!confirmed) return;
    btn.disabled = true;
    try {
      var res = await fetch("/comercial/presentaciones/" + encodeURIComponent(id) + "/eliminar", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      var data = await readJson(res);
      if (res.ok && data.state === "ok") {
        var row = btn.closest("tr");
        if (row) {
          row.remove();
          document.dispatchEvent(new CustomEvent("cp:rows-changed"));
          toast("Presentación eliminada", "«" + title + "» ya no está en el catálogo.", "success");
        } else {
          try { window.sessionStorage.setItem(FLASH_KEY, "«" + title + "» ya no está en el catálogo."); } catch (e) { /* ver arriba */ }
          window.location.assign(data.url || "/comercial/presentaciones");
        }
        return;
      }
      toast("No se pudo eliminar", messageOf(data, "Reintentá en unos minutos."), "punch");
    } catch (e) {
      toast("No se pudo eliminar", "Verificá tu conexión y reintentá.", "punch");
    }
    btn.disabled = false;
  });

  // ── Componentes reutilizables: Ver (vista previa con el renderer real) · Duplicar · Editar (textos y tonos) · Eliminar ───────────────────────────────────────────────────────
  // Misma mecánica que Presentaciones: diálogos nativos del Hub, POST de mismo origen, autoridad en el servidor. El formulario de edición se arma a partir de los parámetros del componente con `createElement` +
  // `value` (sin armar markup con cadenas) y el payload conserva la ESTRUCTURA original: solo se reemplazan textos y tonos. El servidor revalida contra el contrato del renderer y exige la misma estructura.
  var CC_FLASH = "cc-flash";
  try {
    var ccFlash = window.sessionStorage.getItem(CC_FLASH);
    if (ccFlash) { window.sessionStorage.removeItem(CC_FLASH); var f = JSON.parse(ccFlash); toast(f.title, f.text, "success"); }
  } catch (e) { /* sin sessionStorage: el aviso se omite, la acción ya ocurrió */ }
  function ccReload(title, text) {
    try { window.sessionStorage.setItem(CC_FLASH, JSON.stringify({ title: title, text: text })); } catch (e) { /* ver arriba */ }
    window.location.reload();
  }
  var STACK_TONES = [["light", "Destacada suave"], ["strong", "Destacada"], ["neutral", "Neutra"]];
  var BOX_TONES = [["gold", "Dorada"], ["neutral", "Neutra"]];
  var ccUid = 0;

  function ccField(labelText, control) {
    var wrap = document.createElement("div");
    wrap.className = "feedback-field";
    var label = document.createElement("label");
    ccUid += 1;
    control.id = "cc-f-" + ccUid;
    label.setAttribute("for", control.id);
    label.textContent = labelText;
    wrap.appendChild(label);
    wrap.appendChild(control);
    return wrap;
  }
  function ccInput(path, value) {
    var input = document.createElement("input");
    input.type = "text"; input.maxLength = 120; input.autocomplete = "off"; input.required = true;
    input.value = value;
    input.setAttribute("data-cc-path", path);
    return input;
  }
  function ccSelect(path, value, options) {
    var select = document.createElement("select");
    options.forEach(function (o) { var opt = document.createElement("option"); opt.value = o[0]; opt.textContent = o[1]; select.appendChild(opt); });
    select.value = value;
    select.setAttribute("data-cc-path", path);
    return select;
  }
  function buildParamsForm(host, params) {
    host.replaceChildren();
    if (params.sides.length) {
      var sides = document.createElement("fieldset");
      sides.className = "cc-edit-layer";
      var sl = document.createElement("legend"); sl.textContent = "Laterales"; sides.appendChild(sl);
      var sg = document.createElement("div"); sg.className = "cc-edit-grid";
      params.sides.forEach(function (text, i) { sg.appendChild(ccField("Lateral " + (i + 1), ccInput("sides." + i, text))); });
      sides.appendChild(sg); host.appendChild(sides);
    }
    params.layers.forEach(function (layer, li) {
      var fs = document.createElement("fieldset");
      fs.className = "cc-edit-layer";
      var legend = document.createElement("legend"); legend.textContent = "Capa " + (li + 1); fs.appendChild(legend);
      var grid = document.createElement("div"); grid.className = "cc-edit-grid";
      grid.appendChild(ccField("Rótulo", ccInput("layers." + li + ".label", layer.label)));
      if ("sub" in layer) grid.appendChild(ccField("Descripción", ccInput("layers." + li + ".sub", layer.sub)));
      grid.appendChild(ccField("Tono de la capa", ccSelect("layers." + li + ".tone", layer.tone, STACK_TONES)));
      fs.appendChild(grid);
      layer.boxes.forEach(function (box, bi) {
        var bg = document.createElement("div"); bg.className = "cc-edit-grid";
        bg.appendChild(ccField("Caja " + (bi + 1), ccInput("layers." + li + ".boxes." + bi + ".text", box.text)));
        bg.appendChild(ccField("Tono de la caja " + (bi + 1), ccSelect("layers." + li + ".boxes." + bi + ".tone", box.tone, BOX_TONES)));
        if ("note" in box) bg.appendChild(ccField("Nota de la caja " + (bi + 1), ccInput("layers." + li + ".boxes." + bi + ".note", box.note)));
        fs.appendChild(bg);
      });
      host.appendChild(fs);
    });
  }
  function readParamsForm(host, original) {
    var params = JSON.parse(JSON.stringify(original));                           // la estructura (capas, cajas, reveal, conectores) sale del original; solo se pisan textos y tonos
    Array.prototype.forEach.call(host.querySelectorAll("[data-cc-path]"), function (control) {
      var parts = control.getAttribute("data-cc-path").split(".");
      var target = params;
      for (var k = 0; k < parts.length - 1; k += 1) target = target[parts[k]];
      target[parts[parts.length - 1]] = control.value;
    });
    return params;
  }
  function ccPost(url, payload) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(function (res) {
      return readJson(res).then(function (data) { return { ok: res.ok && data.state === "ok", status: res.status, data: data }; });
    });
  }
  function ccShowError(dialog, text) {
    var box = dialog.querySelector("[data-cc-error]");
    box.textContent = text || "";
    box.hidden = !text;
  }

  var viewDialog = document.getElementById("cc-view-dialog");
  var dupDialog = document.getElementById("cc-dup-dialog");
  var editDialog = document.getElementById("cc-edit-dialog");
  var ccEditing = null;

  document.addEventListener("click", function (ev) {
    var view = ev.target.closest("[data-cc-view]");
    if (view && viewDialog) {
      viewDialog.querySelector("[data-cc-view-title]").textContent = view.getAttribute("data-cc-name") || "Componente";
      viewDialog.querySelector("[data-cc-view-meta]").textContent = view.getAttribute("data-cc-meta") || "";
      var viewFrame = viewDialog.querySelector("[data-cc-view-frame]");
      viewFrame.src = "/comercial/componentes/vista?ref=" + encodeURIComponent(view.getAttribute("data-cc-view"));
      viewDialog.showModal();
      return;
    }
    var dup = ev.target.closest("[data-cc-dup]");
    if (dup && !dup.disabled && dupDialog) {
      dupDialog.dataset.ref = dup.getAttribute("data-cc-dup");
      var nameInput = dupDialog.querySelector("[data-cc-name-input]");
      nameInput.value = ("Copia de " + (dup.getAttribute("data-cc-name") || "")).slice(0, nameInput.maxLength > 0 ? nameInput.maxLength : 120);
      dupDialog.querySelector("[data-cc-desc-input]").value = "";
      ccShowError(dupDialog, "");
      dupDialog.querySelector("[data-cc-dup-submit]").disabled = false;
      dupDialog.showModal();
      nameInput.focus(); nameInput.select();
      return;
    }
    var edit = ev.target.closest("[data-cc-edit]");
    if (edit && !edit.disabled && editDialog) {
      var params;
      try { params = JSON.parse(edit.getAttribute("data-cc-params")); } catch (e) { return; }
      ccEditing = { id: edit.getAttribute("data-cc-edit"), revision: parseInt(edit.getAttribute("data-cc-rev"), 10), params: params };
      editDialog.querySelector("[data-cc-name-input]").value = edit.getAttribute("data-cc-name") || "";
      editDialog.querySelector("[data-cc-desc-input]").value = edit.getAttribute("data-cc-desc") || "";
      buildParamsForm(editDialog.querySelector("[data-cc-edit-fields]"), params);
      ccShowError(editDialog, "");
      editDialog.querySelector("[data-cc-edit-submit]").disabled = false;
      editDialog.showModal();
    }
  });
  if (viewDialog) {
    // Vista previa a escala proporcional: el iframe (renderer REAL) se maqueta al ancho de referencia de la presentación (su ancho CSS) y se escala al ancho disponible; la caja toma la altura escalada. Así el
    // responsive del renderer (que depende del ancho del viewport) nunca reorganiza capas, cajas ni conectores. No se toca el responsive de la presentación real.
    var vFrame = viewDialog.querySelector("[data-cc-view-frame]"), vBox = viewDialog.querySelector("[data-cc-view-box]");
    var fitPreview = function () {
      if (!viewDialog.open || !vFrame.getAttribute("src")) return;
      var doc = vFrame.contentDocument;
      var h = doc && doc.documentElement ? Math.max(doc.documentElement.scrollHeight, 120) : 640;
      vFrame.style.height = h + "px";
      var k = Math.min(1, vBox.clientWidth / vFrame.offsetWidth);
      vFrame.style.transform = "scale(" + k + ")";
      vBox.style.height = Math.ceil(h * k) + "px";
    };
    vFrame.addEventListener("load", function () {
      fitPreview();
      var fonts = vFrame.contentDocument && vFrame.contentDocument.fonts;
      if (fonts && fonts.ready) fonts.ready.then(fitPreview);                                                   // segunda pasada con las fuentes ya aplicadas (altura final)
    });
    window.addEventListener("resize", fitPreview);
    viewDialog.addEventListener("close", function () { vFrame.removeAttribute("src"); vFrame.style.transform = ""; vBox.style.height = ""; });
  }

  if (dupDialog) {
    var dupBtn = dupDialog.querySelector("[data-cc-dup-submit]");
    var duplicate = async function () {
      var name = dupDialog.querySelector("[data-cc-name-input]").value.trim();
      if (!name) { ccShowError(dupDialog, "Ingresá un nombre para el componente."); return; }
      dupBtn.disabled = true;
      try {
        var r = await ccPost("/comercial/componentes/duplicar", { ref: dupDialog.dataset.ref, name: name, description: dupDialog.querySelector("[data-cc-desc-input]").value.trim() });
        if (r.ok) { ccReload("Componente duplicado", "«" + name + "» ya está en «Componentes reutilizables»."); return; }
        ccShowError(dupDialog, messageOf(r.data, "No se pudo duplicar el componente."));
      } catch (e) {
        ccShowError(dupDialog, "No se pudo duplicar el componente. Verificá tu conexión y reintentá.");
      }
      dupBtn.disabled = false;
    };
    dupBtn.addEventListener("click", duplicate);
    dupDialog.querySelector("form").addEventListener("keydown", function (ev) { if (ev.key === "Enter" && ev.target.matches("input")) { ev.preventDefault(); duplicate(); } });
  }

  if (editDialog) {
    var editBtn = editDialog.querySelector("[data-cc-edit-submit]");
    var saveComponent = async function () {
      if (!ccEditing) return;
      var form = editDialog.querySelector("form");
      if (!form.reportValidity()) return;                                        // campos vacíos: el navegador lo marca (el servidor valida de nuevo)
      editBtn.disabled = true;
      try {
        var payload = { revision: ccEditing.revision, name: editDialog.querySelector("[data-cc-name-input]").value.trim(), description: editDialog.querySelector("[data-cc-desc-input]").value.trim(),
                        params: readParamsForm(editDialog.querySelector("[data-cc-edit-fields]"), ccEditing.params) };
        var r = await ccPost("/comercial/componentes/" + encodeURIComponent(ccEditing.id), payload);
        if (r.ok) { ccReload("Componente guardado", "Revisión " + r.data.revision + ". Lo ya insertado en presentaciones no cambia."); return; }
        ccShowError(editDialog, messageOf(r.data, "No se pudo guardar el componente."));
      } catch (e) {
        ccShowError(editDialog, "No se pudo guardar el componente. Verificá tu conexión y reintentá; tus cambios en pantalla no se perdieron.");
      }
      editBtn.disabled = false;
    };
    editBtn.addEventListener("click", saveComponent);
  }

  document.addEventListener("click", async function (ev) {
    var btn = ev.target.closest("[data-cc-delete]");
    if (!btn || btn.disabled) return;
    var name = btn.getAttribute("data-cc-name") || "este componente";
    var message = "Se eliminará «" + name + "» de forma definitiva. Las presentaciones donde ya lo insertaste conservan su copia y no se alteran.";
    var confirmed = typeof dwConfirm === "function"
      ? await dwConfirm({ title: "Eliminar componente", message: message, confirmLabel: "Eliminar" })
      : window.confirm(message);
    if (!confirmed) return;
    btn.disabled = true;
    try {
      var r = await ccPost("/comercial/componentes/" + encodeURIComponent(btn.getAttribute("data-cc-delete")) + "/eliminar", {});
      if (r.ok) {
        var row = btn.closest("tr");
        if (row) row.remove();
        document.dispatchEvent(new CustomEvent("cp:rows-changed"));
        toast("Componente eliminado", "«" + name + "» ya no está en la colección.", "success");
        return;
      }
      toast("No se pudo eliminar", messageOf(r.data, "Reintentá en unos minutos."), "punch");
    } catch (e) {
      toast("No se pudo eliminar", "Verificá tu conexión y reintentá.", "punch");
    }
    btn.disabled = false;
  });

  // ── Copiar enlace (la URL de acceso autenticado; no es una credencial) ───────────────────────────────────────────────
  document.addEventListener("click", async function (ev) {
    var btn = ev.target.closest("[data-cp-copy-link]");
    if (!btn) return;
    var url = window.location.origin + btn.getAttribute("data-cp-copy-link");
    try {
      await navigator.clipboard.writeText(url);
      toast("Enlace copiado", "Quien lo reciba lo abre con su propia sesión del Hub.", "success");
    } catch (e) {
      window.prompt("Copiá el enlace:", url);
    }
  });

  // ── Copiar como nueva ──────────────────────────────────────────────────────────────────────────────────────────────
  var dialog = document.getElementById("cp-copy-dialog");
  if (dialog) {
    var titleInput = dialog.querySelector("[data-cp-title]");
    var errorEl = dialog.querySelector("[data-cp-copy-error]");
    var submitBtn = dialog.querySelector("[data-cp-copy-submit]");
    var logoInput = dialog.querySelector("[data-cp-logo]");
    var LOGO_MAX = 1024 * 1024;

    document.addEventListener("click", function (ev) {
      var opener = ev.target.closest("[data-cp-copy-open]");
      if (!opener) return;
      dialog.dataset.sourceId = opener.getAttribute("data-cp-id") || "";
      titleInput.value = (opener.getAttribute("data-cp-title") || "").slice(0, titleInput.maxLength > 0 ? titleInput.maxLength : 120);
      errorEl.hidden = true;
      errorEl.textContent = "";
      if (logoInput) logoInput.value = "";
      submitBtn.disabled = false;
      dialog.showModal();
      titleInput.focus();
      titleInput.select();
    });

    async function createCopy() {
      var title = titleInput.value.trim();
      if (!title) {
        errorEl.textContent = "Ingresá un título para la copia.";
        errorEl.hidden = false;
        return;
      }
      // Logo opcional: chequeo rápido de tipo y tamaño (el servidor valida de nuevo: firma PNG, integridad y transparencia real).
      var logo = logoInput && logoInput.files && logoInput.files[0];
      if (logo && !(logo.type === "image/png" || /\.png$/i.test(logo.name))) {
        errorEl.textContent = "El logo debe ser un archivo .png con fondo transparente.";
        errorEl.hidden = false;
        return;
      }
      if (logo && logo.size > LOGO_MAX) {
        errorEl.textContent = "El logo supera 1 MB: exportalo más liviano.";
        errorEl.hidden = false;
        return;
      }
      var form = new FormData();                                                 // sin Content-Type propio: el navegador arma el boundary
      form.append("title", title);
      if (logo) form.append("logo", logo, logo.name);
      submitBtn.disabled = true;
      try {
        var res = await fetch("/comercial/presentaciones/" + encodeURIComponent(dialog.dataset.sourceId) + "/copiar", {
          method: "POST", credentials: "same-origin", body: form,
        });
        var data = await readJson(res);
        if (res.ok && data.url) {
          window.location.assign(data.url);
          return;
        }
        errorEl.textContent = messageOf(data, "No se pudo crear la copia.");
      } catch (e) {
        errorEl.textContent = "No se pudo crear la copia. Verificá tu conexión y reintentá.";
      }
      errorEl.hidden = false;
      submitBtn.disabled = false;
    }

    submitBtn.addEventListener("click", createCopy);
    dialog.querySelector("form").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && ev.target === titleInput) { ev.preventDefault(); createCopy(); }
    });
  }

  // ── Editor INLINE (como «02. Estrategia»): los textos son leaves `contenteditable` del propio documento (iframe del mismo origen, `?embed=1&edit=1`); acá viven la barra y el guardado ────────────────────────────────
  // Guarda SOLO los campos que cambiaron, bajo la revisión que cargó la página (R6: el servidor valida longitudes, obligatorios y propiedad). Texto plano de una sola línea (`textContent`).
  var editor = document.querySelector("[data-cp-editor]");
  if (editor) {
    var frame = editor.querySelector("[data-cp-frame]");
    var saveBtn = editor.querySelector("[data-cp-save]");
    var cancelBtn = editor.querySelector("[data-cp-cancel]");
    var dirtyBadge = editor.querySelector("[data-cp-dirty]");
    var errBox = editor.querySelector("[data-cp-error]");
    var saving = false;

    function leaves() { return frame.contentDocument ? Array.prototype.slice.call(frame.contentDocument.querySelectorAll("[data-cp-field]")) : []; }
    function valueOf(el) {
      return el.textContent.replace(/\u00a0/g, " ").replace(/[\r\n]+/g, " ");
    }
    function changed() {
      var out = {};
      leaves().forEach(function (el) { var v = valueOf(el); if (v !== el.getAttribute("data-original")) out[el.getAttribute("data-cp-field")] = v; });
      return out;
    }
    // Slides eliminadas de la copia: ids de las `section[data-cp-removed]` del documento. `originalRemoved` = lo que el servidor renderizó al abrir (o lo último guardado).
    var originalRemoved = [];
    function removedNow() {
      return frame.contentDocument ? Array.prototype.slice.call(frame.contentDocument.querySelectorAll("section[data-cp-removed]")).map(function (s) { return s.id; }) : [];
    }
    function slidesChanged() { return removedNow().join(",") !== originalRemoved.join(","); }
    // Composición por región: solo los DESVÍOS del ocupante por defecto (`null` = quitado), igual que los guarda el servidor.
    var originalBlocks = "{}";
    function blocksNow() {
      var out = {};
      if (!frame.contentDocument) return out;
      Array.prototype.forEach.call(frame.contentDocument.querySelectorAll("[data-cp-region]"), function (r) {
        var occ = r.getAttribute("data-cp-occupant"), def = r.getAttribute("data-cp-default");
        if (occ === def) return;
        var parts = occ.split(":");
        out[r.getAttribute("data-cp-region")] = occ ? { component: parts[0], variant: parts.slice(1).join(":") } : null;
      });
      return out;
    }
    function blocksChanged() { return JSON.stringify(blocksNow()) !== originalBlocks; }
    function refresh() {
      var dirty = Object.keys(changed()).length > 0 || slidesChanged() || blocksChanged();
      dirtyBadge.hidden = !dirty;
      saveBtn.disabled = saving || !dirty;
      return dirty;
    }
    function showError(text) { errBox.textContent = text; errBox.hidden = !text; }

    // QR de la copia: el archivo se sube APARTE (multipart, con la revisión vigente) y se aplica al instante; el servidor valida el PNG, publica el asset y recién después guarda la referencia. Los textos sin guardar
    // siguen en pantalla; la revisión que devuelve el servidor reemplaza a la del editor.
    var QR_MAX = 1024 * 1024;
    async function sendQr(file, remove) {
      showError("");
      var form = new FormData();
      form.append("revision", editor.getAttribute("data-revision"));
      if (remove) form.append("remove", "1"); else form.append("file", file, file.name);
      toast("Guardando QR…", "", "note");
      try {
        var res = await fetch(editor.getAttribute("data-qr-url"), { method: "POST", credentials: "same-origin", body: form });
        var data = await readJson(res);
        if (res.ok && data.state === "ok") {
          editor.setAttribute("data-revision", String(data.revision));
          var doc = frame.contentDocument;
          var img = doc && doc.querySelector("[data-cp-qr]");
          if (img) img.setAttribute("src", data.qr_url);
          var reset = doc && doc.querySelector("[data-cp-qr-reset]");
          if (reset) reset.hidden = !data.qr_custom;
          toast(remove ? "QR institucional" : "QR actualizado", "Revisión " + data.revision + ".", "success");
        } else {
          toast("", "", "");
          showError(messageOf(data, "No se pudo guardar el QR."));
        }
      } catch (e) {
        toast("", "", "");
        showError("No se pudo guardar el QR. Verificá tu conexión y reintentá.");
      }
    }
    function onFrameClick(ev) {
      var t = ev.target.closest && ev.target.closest("[data-cp-qr-replace], [data-cp-qr-reset]");
      if (!t) return;
      if (t.hasAttribute("data-cp-qr-replace")) frame.contentDocument.querySelector("[data-cp-qr-file]").click();
      else sendQr(null, true);
    }
    function onFrameChange(ev) {
      var input = ev.target.closest && ev.target.closest("[data-cp-qr-file]");
      if (!input || !input.files || !input.files[0]) return;
      var file = input.files[0];
      input.value = "";
      if (!(file.type === "image/png" || /\.png$/i.test(file.name))) { showError("El QR debe ser un archivo .png."); return; }
      if (file.size > QR_MAX) { showError("El QR supera 1 MB: exportalo más liviano."); return; }
      sendQr(file, false);
    }

    frame.addEventListener("load", function () {
      if (!frame.contentDocument) return;
      frame.contentDocument.addEventListener("input", refresh);
      frame.contentDocument.addEventListener("cp:slides-changed", refresh);
      frame.contentDocument.addEventListener("cp:composition-changed", refresh);
      frame.contentDocument.addEventListener("click", onFrameClick);
      frame.contentDocument.addEventListener("change", onFrameChange);
      originalRemoved = removedNow();
      originalBlocks = JSON.stringify(blocksNow());
      refresh();
    });

    window.addEventListener("beforeunload", function (ev) {
      if (!saving && refresh()) { ev.preventDefault(); ev.returnValue = ""; }
    });

    cancelBtn.addEventListener("click", async function () {
      var back = editor.getAttribute("data-back-url");
      if (refresh()) {
        var ok = typeof dwConfirm === "function"
          ? await dwConfirm({ title: "Descartar cambios", message: "Tenés cambios sin guardar. Si salís ahora se pierden.", confirmLabel: "Descartar" })
          : window.confirm("Tenés cambios sin guardar. Si salís ahora se pierden.");
        if (!ok) return;
      }
      saving = true;                                                             // evita el aviso nativo de beforeunload: ya se confirmó
      window.location.assign(back);
    });

    saveBtn.addEventListener("click", async function () {
      showError("");
      var delta = changed();
      var slidesDirty = slidesChanged();
      var blocksDirty = blocksChanged();
      if (!Object.keys(delta).length && !slidesDirty && !blocksDirty) { toast("Sin cambios", "No hay cambios para guardar.", "note"); return; }
      saving = true;
      saveBtn.disabled = true;
      toast("Guardando…", "", "note");
      try {
        var res = await fetch(editor.getAttribute("data-save-url"), {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(Object.assign({ revision: parseInt(editor.getAttribute("data-revision"), 10), fields: delta }, slidesDirty ? { removed_slides: removedNow() } : {}, blocksDirty ? { blocks: blocksNow() } : {})),
        });
        var data = await readJson(res);
        if (res.ok && data.state === "ok") {
          editor.setAttribute("data-revision", String(data.revision));
          if (Array.isArray(data.removed_slides)) originalRemoved = data.removed_slides.slice();
          originalBlocks = JSON.stringify(blocksNow());
          leaves().forEach(function (el) {
            var key = el.getAttribute("data-cp-field");
            if (data.content && typeof data.content[key] === "string") el.setAttribute("data-original", data.content[key]);
          });
          toast("Guardado", "Revisión " + data.revision + ".", "success");
        } else {
          toast("", "", "");
          showError(messageOf(data, "No se pudo guardar."));
        }
      } catch (e) {
        toast("", "", "");
        showError("No se pudo guardar. Verificá tu conexión y reintentá; tus cambios en pantalla no se perdieron.");
      }
      saving = false;
      refresh();
    });
  }
})();

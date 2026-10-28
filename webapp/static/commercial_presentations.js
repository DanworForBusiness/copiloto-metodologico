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
    var count = document.querySelector("[data-cp-count]");
    var empty = document.querySelector("[data-cp-empty]");
    var wrap = document.querySelector("[data-cp-table-wrap]");
    function apply() {
      var rows = Array.prototype.slice.call(document.querySelectorAll("#cp-catalog-table tbody tr[data-cp-search]"));    // se relee: una fila eliminada ya no cuenta
      var q = dwSearchFold(input.value);
      var shown = 0;
      rows.forEach(function (tr) {
        var hit = !q || dwSearchFold(tr.getAttribute("data-cp-search")).indexOf(q) !== -1;
        tr.hidden = !hit;
        if (hit) shown += 1;
      });
      if (count) count.textContent = shown + " presentaci" + (shown === 1 ? "ón" : "ones");
      if (wrap) wrap.hidden = shown === 0;
      if (empty) empty.hidden = shown !== 0;
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

    document.addEventListener("click", function (ev) {
      var opener = ev.target.closest("[data-cp-copy-open]");
      if (!opener) return;
      dialog.dataset.sourceId = opener.getAttribute("data-cp-id") || "";
      titleInput.value = (opener.getAttribute("data-cp-title") || "").slice(0, titleInput.maxLength > 0 ? titleInput.maxLength : 120);
      errorEl.hidden = true;
      errorEl.textContent = "";
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
      submitBtn.disabled = true;
      try {
        var res = await fetch("/comercial/presentaciones/" + encodeURIComponent(dialog.dataset.sourceId) + "/copiar", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: title }),
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

  // ── Editor: guarda SOLO los campos que cambiaron, bajo la revisión que cargó la página ───────────────────────────────
  var form = document.querySelector("[data-cp-edit-form]");
  if (form) {
    var saveBtn = form.querySelector("[data-cp-save]");
    var errBox = form.querySelector("[data-cp-error]");
    var fields = Array.prototype.slice.call(form.querySelectorAll("[data-cp-field]"));
    var saving = false;

    function changed() {
      var out = {};
      fields.forEach(function (f) { if (f.value !== f.getAttribute("data-original")) out[f.name] = f.value; });
      return out;
    }
    function showError(text) { errBox.textContent = text; errBox.hidden = !text; }

    window.addEventListener("beforeunload", function (ev) {
      if (!saving && Object.keys(changed()).length) { ev.preventDefault(); ev.returnValue = ""; }
    });

    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      showError("");
      var delta = changed();
      if (!Object.keys(delta).length) { toast("Sin cambios", "No hay cambios para guardar.", "note"); return; }
      saving = true;
      saveBtn.disabled = true;
      toast("Guardando…", "", "note");
      try {
        var res = await fetch(form.getAttribute("data-save-url"), {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: parseInt(form.getAttribute("data-revision"), 10), fields: delta }),
        });
        var data = await readJson(res);
        if (res.ok && data.state === "ok") {
          form.setAttribute("data-revision", String(data.revision));
          fields.forEach(function (f) {
            if (data.content && typeof data.content[f.name] === "string") { f.value = data.content[f.name]; f.setAttribute("data-original", data.content[f.name]); }
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
      saveBtn.disabled = false;
    });
  }
})();

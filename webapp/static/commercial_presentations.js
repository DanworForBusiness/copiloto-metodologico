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

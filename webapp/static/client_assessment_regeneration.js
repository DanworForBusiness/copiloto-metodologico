/* Informe de Cliente → «Regenerar N Assessments» (ver webapp/services/client_assessment_regeneration.py).
   Delegado en document (sobrevive a los swaps de HTMX del slot de detalle). Confirma con el diálogo compartido del Hub (`dwConfirm`), arranca UNA
   operación consolidada (POST same-origin; el servidor resuelve el cliente desde el flujo) y sigue su avance con `/copilot/operations/{run_id}`:
   la operación corre en el servidor y no depende de esta pestaña. Al terminar muestra el resumen y vuelve a pedir el consolidado (indicadores al día).
   Anti doble clic: el botón se deshabilita al primer clic; el servidor además admite UNA sola operación por cliente.
   Estados de presentación (un solo elemento visible por estado; lo oculto usa `hidden`, sin reservar espacio):
     reposo ..... botón habilitado
     iniciando .. botón deshabilitado + aviso «Iniciando regeneración…» (spinner) AL LADO del botón
     ejecutando . texto «Regenerando X de N · Modelo (resultado)» y, debajo, la barra
     finalizado . sin barra; resumen actualizados / omitidos / fallidos; luego se vuelve a pedir el consolidado
     error ...... mensaje comprensible al lado del botón (sin spinner) y botón habilitado para reintentar */
(function () {
  var POLL_MS = 1200;
  var RESULT_TEXT = { updated: "actualizado", skipped: "omitido", failed: "falló" };

  function confirmBox(opts) {
    if (typeof window.dwConfirm === "function") return window.dwConfirm(opts);
    if (typeof dwConfirm === "function") return dwConfirm(opts);              // eslint-disable-line no-undef
    return Promise.resolve(window.confirm(opts.message || opts.title));
  }

  function q(root, sel) { return root.querySelector(sel); }

  /* Aviso al lado del botón: `busy` = iniciando (con spinner); sin `busy` = error (sin spinner). `text` vacío lo oculta. */
  function setNote(root, text, busy) {
    var note = q(root, "[data-cli-regen-note]");
    if (!note) return;
    var spinner = q(note, "[data-cli-regen-spinner]");
    var label = q(note, "[data-cli-regen-note-text]");
    if (label) label.textContent = text || "";
    if (spinner) spinner.hidden = !busy;
    note.hidden = !text;
  }

  function showProgress(root, visible) {
    var p = q(root, "[data-cli-regen-progress]");
    if (p) p.hidden = !visible;
  }

  function setStatus(root, text) {
    var s = q(root, "[data-cli-regen-status]");
    if (s) s.textContent = text;
  }

  function setProgress(root, done, total) {
    var bar = q(root, "[data-cli-regen-progress] .progress");
    if (!bar) return;
    var pct = total ? Math.round((done / total) * 100) : 0;
    bar.setAttribute("aria-valuenow", String(done));
    bar.setAttribute("aria-valuemax", String(total));
    var fill = bar.querySelector("span");
    if (fill) fill.style.width = pct + "%";
  }

  function refresh(root) {
    var url = root.getAttribute("data-refresh-url");
    var slot = document.getElementById("catalog-detail-slot");
    if (url && slot && window.htmx) window.htmx.ajax("GET", url, { target: slot, swap: "innerHTML" });
  }

  function fail(root, button, text) {
    showProgress(root, false);
    setNote(root, text, false);
    if (button) button.disabled = false;                                        // reintentable: el servidor vuelve a validar elegibles, concurrencia y transición
  }

  function running(root, done, total, am, result) {
    setNote(root, "", false);
    showProgress(root, true);
    setProgress(root, done, total);
    setStatus(root, am ? "Regenerando " + done + " de " + total + " · " + am + " (" + (RESULT_TEXT[result] || result) + ")"
                       : "Regenerando " + done + " de " + total + "…");
  }

  function finish(root, ev) {
    var s = ev.summary || {};
    showProgress(root, false);                                                  // sin barra obsoleta
    setNote(root, "", false);
    var summary = q(root, "[data-cli-regen-summary]");
    if (summary) {
      summary.textContent = "Regeneración terminada: " + (s.updated || 0) + " actualizados · " + (s.skipped || 0) + " omitidos · " + (s.failed || 0) + " fallidos." +
        (s.failed ? " Los que fallaron conservan su informe anterior y se pueden volver a regenerar." : "") +
        (ev.state === "FAILED" && !s.total && ev.message ? " " + ev.message : "");
      summary.hidden = false;
    }
    window.setTimeout(function () { refresh(root); }, 1800);                   // el consolidado recarga indicadores y, si quedan pendientes, el botón
  }

  function follow(root, button, runId, total) {
    var since = 0;
    function tick() {
      fetch("/copilot/operations/" + encodeURIComponent(runId) + "?since=" + since, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) {
          if (r.status === 404) { var e = new Error("gone"); e.gone = true; throw e; }
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          since = data.next;
          var last = null;
          (data.events || []).forEach(function (ev) {
            if (ev.t === "progress") running(root, ev.done, ev.total || total, ev.am, ev.result);
            else if (ev.t === "done") last = ev;
          });
          if (last) { finish(root, last); return; }
          window.setTimeout(tick, POLL_MS);
        })
        .catch(function (err) {
          if (err && err.gone) {                                                // la operación ya no está disponible para consultar: se recarga el consolidado (estado real)
            fail(root, button, "No se pudo seguir el avance de la regeneración. Actualizando el informe…");
            window.setTimeout(function () { refresh(root); }, 1800);
            return;
          }
          setStatus(root, "Reintentando la consulta del avance…");             // corte transitorio: la operación sigue en el servidor
          window.setTimeout(tick, POLL_MS * 3);
        });
    }
    tick();
  }

  function start(root, button) {
    var n = root.getAttribute("data-count") || "";
    confirmBox({
      title: "Regenerar " + n + " Assessments",
      message: "Se regenerarán " + n + " Assessments desactualizados de este cliente con la metodología y la evidencia vigentes, con el mismo procedimiento que el informe individual. " +
        "Los vigentes no se tocan; si alguno falla, conserva su informe anterior. ¿Continuar?",
      confirmLabel: "Regenerar",
      okClass: "btn-primary",                                                   // acción no destructiva (mismo criterio que «Reclasificar»)
    }).then(function (ok) {
      if (!ok) { button.disabled = false; return; }
      var summary = q(root, "[data-cli-regen-summary]");
      if (summary) summary.hidden = true;
      setNote(root, "Iniciando regeneración…", true);
      fetch(root.getAttribute("data-regen-url"), { method: "POST", credentials: "same-origin", headers: { "Accept": "application/json", "HX-Request": "true" } })
        .then(function (r) { return r.json().then(function (body) { return { status: r.status, body: body }; }); })
        .then(function (res) {
          var b = res.body || {};
          var total = b.total || Number(n) || 0;
          if (b.run_id) { running(root, 0, total); follow(root, button, b.run_id, total); return; }
          if (b.outcome === "NOTHING_TO_DO") { setNote(root, b.message || "No hay Assessments desactualizados.", false); refresh(root); return; }
          fail(root, button, b.message || "No se pudo iniciar la regeneración. Reintentá en unos minutos.");
        })
        .catch(function () { fail(root, button, "No se pudo iniciar la regeneración. Reintentá en unos minutos."); });
    });
  }

  document.addEventListener("click", function (ev) {
    var button = ev.target.closest && ev.target.closest("[data-cli-regen-start]");
    if (!button) return;
    ev.preventDefault();
    if (button.disabled) return;
    button.disabled = true;                                                     // anti doble clic (el servidor también admite una sola operación por cliente)
    var root = button.closest("[data-cli-regen]");
    if (root) start(root, button);
  });
})();

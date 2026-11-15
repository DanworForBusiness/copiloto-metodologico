/* Informe de Cliente → «Regenerar N Assessments» (ver webapp/services/client_assessment_regeneration.py).
   Delegado en document (sobrevive a los swaps de HTMX del slot de detalle). Confirma con el diálogo compartido del Hub (`dwConfirm`), arranca UNA
   operación consolidada (POST same-origin; el servidor resuelve el cliente desde el flujo) y sigue su avance con `/copilot/operations/{run_id}`:
   la operación corre en el servidor y no depende de esta pestaña. Al terminar muestra el resumen y vuelve a pedir el consolidado (indicadores al día).
   Anti doble clic: el botón se deshabilita al primer clic; el servidor además admite UNA sola operación por cliente. */
(function () {
  var POLL_MS = 1200;

  function confirmBox(opts) {
    if (typeof window.dwConfirm === "function") return window.dwConfirm(opts);
    if (typeof dwConfirm === "function") return dwConfirm(opts);              // eslint-disable-line no-undef
    return Promise.resolve(window.confirm(opts.message || opts.title));
  }

  function setStatus(root, text) {
    var s = root.querySelector("[data-cli-regen-status]");
    if (s) s.textContent = text;
  }

  function setProgress(root, done, total) {
    var bar = root.querySelector("[data-cli-regen-progress] .progress");
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

  function finish(root, ev) {
    var s = ev.summary || {};
    var summary = root.querySelector("[data-cli-regen-summary]");
    if (summary) {
      summary.hidden = false;
      summary.textContent = "Regeneración terminada: " + (s.updated || 0) + " actualizados · " + (s.skipped || 0) + " omitidos · " + (s.failed || 0) + " fallidos." +
        (s.failed ? " Los que fallaron conservan su informe anterior." : "");
    }
    setStatus(root, ev.message || "");
    window.setTimeout(function () { refresh(root); }, 1800);
  }

  function follow(root, runId, total) {
    var since = 0;
    function tick() {
      fetch("/copilot/operations/" + encodeURIComponent(runId) + "?since=" + since, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (data) {
          since = data.next;
          var last = null;
          (data.events || []).forEach(function (ev) {
            if (ev.t === "progress") {
              setProgress(root, ev.done, ev.total || total);
              setStatus(root, "Regenerando " + ev.done + " de " + (ev.total || total) + " · " + ev.am + " (" + ({updated: "actualizado", skipped: "omitido", failed: "falló"}[ev.result] || ev.result) + ")");
            } else if (ev.t === "done") {
              last = ev;
            }
          });
          if (last) { finish(root, last); return; }
          window.setTimeout(tick, POLL_MS);
        })
        .catch(function () {                                                    // corte transitorio: reintenta sin reiniciar nada (la operación sigue en el servidor)
          setStatus(root, "Reintentando la consulta del avance…");
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
      var progress = root.querySelector("[data-cli-regen-progress]");
      if (progress) progress.hidden = false;
      setStatus(root, "Iniciando…");
      fetch(root.getAttribute("data-regen-url"), { method: "POST", credentials: "same-origin", headers: { "Accept": "application/json", "HX-Request": "true" } })
        .then(function (r) { return r.json().then(function (body) { return { status: r.status, body: body }; }); })
        .then(function (res) {
          var b = res.body || {};
          if (b.run_id) { setProgress(root, 0, b.total || Number(n) || 0); follow(root, b.run_id, b.total || Number(n) || 0); return; }
          setStatus(root, b.message || "No se pudo iniciar la regeneración.");
          if (b.outcome === "NOTHING_TO_DO") refresh(root);
        })
        .catch(function () { setStatus(root, "No se pudo iniciar la regeneración. Reintentá en unos minutos."); button.disabled = false; });
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

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

  /* ── Resultado después de recargar ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
     Al terminar se guarda el resultado SOLO en memoria de esta página (no en sobres, persistencia gobernada ni storage): la recarga del consolidado
     lo muestra una vez, con el aviso de resultado del Hub (`.callout--feedback`, mismo markup que «Catálogo actualizado»), y solo si el cliente del
     informe recargado es el mismo de la operación. Tonos: éxito total (sin fallidos ni omitidos) → success; con fallidos u omitidos y algo actualizado
     → punch (advertencia); nada actualizado → warn (error). La operación ya se vio terminar acá: el aviso global no la repite (`dwCopilotOps`). */
  var lastResult = null;                                                        // { client, state, summary, message }
  var RESULT_TTL_MS = 10 * 60 * 1000;

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function clientOf(el) {
    var slot = el && el.closest ? el.closest("#catalog-detail-slot") : null;
    var anchor = (slot || document).querySelector("[data-cli-regen-result]");
    return anchor ? anchor.getAttribute("data-client") || "" : "";
  }

  function resultView(r) {
    var s = r.summary || {};
    var updated = s.updated || 0, skipped = s.skipped || 0, failed = s.failed || 0, total = s.total || (updated + skipped + failed);
    var counts = plural(updated, "actualizado", "actualizados") + " · " + plural(skipped, "omitido", "omitidos") + " · " + plural(failed, "fallido", "fallidos");
    if (r.state === "SUCCESS" && !failed && !skipped && updated) {
      return { tone: "success", role: "status", title: "Assessments actualizados correctamente",
               lines: ["Se " + (updated === 1 ? "regeneró 1 Assessment" : "regeneraron " + updated + " Assessments") + " de " + r.client + ". Los indicadores del cliente ya reflejan los resultados actualizados.", counts] };
    }
    if (updated) {
      return { tone: "punch", role: "status", title: "Regeneración completada parcialmente",
               lines: ["Se " + (updated === 1 ? "regeneró 1" : "regeneraron " + updated) + " de " + plural(total, "Assessment", "Assessments") + " de " + r.client + "." +
                       (failed ? " Los que fallaron conservan su informe anterior y se pueden volver a regenerar desde este informe." : "") +
                       (skipped ? " Los omitidos ya no estaban desactualizados o se pausaron por una activación de la metodología." : ""), counts] };
    }
    return { tone: "warn", role: "alert", title: "No se pudieron regenerar los Assessments",
             lines: [(total ? "Ninguno de los " + plural(total, "Assessment", "Assessments") + " de " + r.client + " se regeneró; todos conservan su informe anterior."
                            : "La regeneración de " + r.client + " no terminó: los Assessments conservan su informe anterior.") +
                     (failed || !total ? " Podés reintentar desde el aviso «Assessments desactualizados» de este informe." : ""),
                     total ? counts : (r.message || "")].filter(Boolean) };
  }

  function renderResult(anchor, r) {
    var v = resultView(r);
    var box = document.createElement("aside");                                  // mismo markup que «Catálogo actualizado» (datasphere_assessment_refresh.html)
    box.className = "callout callout--" + v.tone + " callout--feedback";
    box.setAttribute("role", v.role);
    var head = document.createElement("div");
    head.className = "callout__head";
    var title = document.createElement("strong");
    title.className = "callout__title";
    title.textContent = v.title;
    head.appendChild(title);
    var body = document.createElement("div");
    body.className = "callout__body";
    v.lines.forEach(function (t) { var p = document.createElement("p"); p.textContent = t; body.appendChild(p); });
    box.appendChild(head);
    box.appendChild(body);
    var close = document.createElement("button");                               // MISMA cruz que los avisos de operaciones del Catálogo (copilot_operations.js)
    close.type = "button";
    close.className = "whatsnew-banner__close";
    close.textContent = "✕";
    close.title = "Cerrar";
    close.setAttribute("aria-label", "Cerrar aviso de regeneración");
    close.addEventListener("click", function () { anchor.replaceChildren(); anchor.hidden = true; });
    box.appendChild(close);
    anchor.replaceChildren(box);
    anchor.hidden = false;
  }

  function showPendingResult(scope) {
    if (!lastResult) return;
    var anchor = (scope || document).querySelector("[data-cli-regen-result]");
    if (!anchor) return;
    var r = lastResult;
    lastResult = null;                                                          // una sola vez: el próximo ingreso al informe ya no lo muestra
    if (Date.now() - r.at > RESULT_TTL_MS) return;
    if ((anchor.getAttribute("data-client") || "").toLowerCase() !== (r.client || "").toLowerCase()) return;   // nunca en el informe de otro cliente
    renderResult(anchor, r);
  }

  document.addEventListener("htmx:afterSettle", function (ev) {
    var t = ev.detail && ev.detail.target;
    if (t && t.id === "catalog-detail-slot") showPendingResult(t);
  });

  function finish(root, ev, runId) {
    lastResult = { client: clientOf(root), state: ev.state, summary: ev.summary || {}, message: ev.message || "", at: Date.now() };
    if (runId && window.dwCopilotOps && window.dwCopilotOps.seen) window.dwCopilotOps.seen(runId);   // ya se vio terminar acá: el aviso global no la repite
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
    window.setTimeout(function () {                                             // el consolidado recarga indicadores y, si quedan pendientes, el botón
      if (document.contains(root)) refresh(root);                               // solo si este informe sigue en pantalla (no saca al usuario de otro informe)
      else lastResult = null;                                                   // ya está en otro lado: el resultado no se arrastra a otro informe
    }, 1800);
  }

  function follow(root, button, runId, total) {
    var since = 0;
    if (window.dwCopilotOps && window.dwCopilotOps.attach) window.dwCopilotOps.attach(runId);   // este panel la sigue: el aviso global no la duplica
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
          if (last) { finish(root, last, runId); return; }
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

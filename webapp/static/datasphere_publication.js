// "Publicar modelo" — publicación de un modelo de SAP Datasphere al Catálogo, con avance LOCAL en tiempo real y apertura
// automática de la pestaña Catálogo al terminar. HERMANO de datasphere_assessment.js (misma mecánica NDJSON) pero con su PROPIO
// panel/estado y su PROPIA ruta: no comparte acción de negocio con el Assessment.
//
// Contrato (ver webapp/routers/datasphere_publication.py): POST (cuerpo urlencoded, jamás query string) → respuesta NDJSON:
//   {"t":"start"} · {"t":"line","v":"<línea del extractor>"}* · {"t":"phase","phase":"<id>","v":"<hito>"}* ·
//   {"t":"done","state","error_code","message","title","errors","result_url"}
// Todo texto externo entra con textContent (nunca como HTML). El Client Secret se lee del formulario, se limpia del campo ANTES de
// enviar y no se guarda en ningún lado. Éxito = el backend entrega `result_url` (pestaña Catálogo) y el navegador la abre.
(function () {
  "use strict";

  // Fases semánticas del backend en las que hay trabajo en curso ("extracting" es la inicial, antes de cualquier evento).
  const WORKING = new Set(["extracting", "publishing"]);

  function el(root, sel) { return root.querySelector(sel); }

  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }

  async function run(form) {
    const panel = form.closest("#dsp-panel");
    const progress = el(panel, "[data-dsp-progress]");
    const status = el(panel, "[data-dsp-status]");
    const spinner = el(panel, "[data-dsp-spinner]");
    const submit = el(form, "[data-dsp-submit]");
    const log = el(panel, "[data-dsp-log]");
    const detail = el(panel, "[data-dsp-detail]");

    // Estado inicial limpio de la corrida
    form.querySelectorAll("[data-dsp-error]").forEach((p) => { p.textContent = ""; p.hidden = true; });
    setHidden(el(panel, "[data-dsp-failed]"), true);
    log.textContent = "";
    detail.open = true;

    // El cuerpo se arma ahora y el secreto se borra del campo YA: no queda en el DOM durante la corrida ni para reintentar.
    const body = new URLSearchParams(new FormData(form));
    form.querySelectorAll('input[type="password"]').forEach((i) => { i.value = ""; });

    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    setHidden(progress, false);
    status.textContent = status.dataset.runningText;
    setHidden(spinner, false);                                 // extracción en curso: misma fila de procesamiento, spinner visible

    let done = null;
    try {
      const response = await fetch(form.getAttribute("action"), {
        method: "POST", body, credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      if (!response.ok || !response.body) {
        throw new Error("HTTP " + response.status);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n")) >= 0) {
          const raw = buffer.slice(0, index);
          buffer = buffer.slice(index + 1);
          if (!raw.trim()) continue;
          const event = JSON.parse(raw);
          if (event.t === "line") {
            log.textContent += event.v + "\n";
            log.scrollTop = log.scrollHeight;
          } else if (event.t === "phase") {
            status.textContent = event.v;                      // hito veraz emitido por el backend (sin porcentajes)
            setHidden(spinner, !WORKING.has(event.phase));   // presentación: el spinner solo gira en fases con trabajo en curso
          } else if (event.t === "done") {
            done = event;
          }
        }
      }
    } catch (error) {
      done = { state: "FAILED", error_code: "CONNECTION_INTERRUPTED", title: "No se pudo publicar el modelo", message: "Se interrumpió la conexión con el servidor. Revisá el detalle técnico e intentá nuevamente.", errors: {} };
    }

    if (done && done.state === "SUCCESS" && done.result_url) {
      window.location.assign(done.result_url);                // la pestaña Catálogo se abre sola; el botón queda deshabilitado hasta salir
      return;
    }

    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    status.textContent = "";
    setHidden(spinner, true);                                  // la falla detiene el estado de procesamiento
    if (!done) return;

    if (done.state === "INVALID") {
      Object.entries(done.errors || {}).forEach(([field, message]) => {
        const target = form.querySelector('[data-dsp-error="' + field + '"]');
        if (target) { target.textContent = message; target.hidden = false; }
      });
      setHidden(progress, true);
    } else {
      el(panel, "[data-dsp-title]").textContent = done.title || "";
      el(panel, "[data-dsp-message]").textContent = done.message || "";
      if (done.error_code) log.textContent += "[" + done.error_code + "]" + String.fromCharCode(10);   // el código técnico vive en "Detalle técnico"
      setHidden(el(panel, "[data-dsp-failed]"), false);
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-dsp-form")) return;
    event.preventDefault();                                   // jamás navegación de formulario clásica
    const submit = form.querySelector("[data-dsp-submit]");
    if (submit && submit.disabled) return;                    // sin doble ejecución por clicks repetidos
    run(form);
  });
})();

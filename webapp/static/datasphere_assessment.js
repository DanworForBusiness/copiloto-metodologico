// "Assessment de tu modelo" — ejecución desde SAP Datasphere con avance LOCAL en tiempo real y apertura automática del informe.
//
// Por qué NO usa HTMX: la ejecución es técnica y determinista, no una respuesta de IA. Tiene su propio panel y su propio estado y no
// comparte ningún indicador ni evento global con las demás interacciones del panel lateral.
//
// Contrato (ver webapp/routers/datasphere_assessment.py): POST (cuerpo urlencoded, jamás query string) → respuesta NDJSON:
//   {"t":"start"} · {"t":"line","v":"<línea del extractor>"}* · {"t":"phase","v":"<hito>"}* ·
//   {"t":"done","state","error_code","message","title","errors","result_url"}
// Todo texto externo entra con textContent (nunca como HTML). El navegador no interpreta ni re-diagnostica: muestra lo que el
// backend emite. El Client Secret se lee del formulario, se limpia del campo ANTES de enviar y no se guarda en ningún lado.
// Éxito = el backend entrega `result_url` y el navegador abre el informe automáticamente.
(function () {
  "use strict";

  function el(root, sel) { return root.querySelector(sel); }

  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }

  async function run(form) {
    const panel = form.closest("#dsa-panel");
    const progress = el(panel, "[data-dsa-progress]");
    const status = el(panel, "[data-dsa-status]");
    const submit = el(form, "[data-dsa-submit]");
    const log = el(panel, "[data-dsa-log]");
    const detail = el(panel, "[data-dsa-detail]");

    // Estado inicial limpio de la corrida
    form.querySelectorAll("[data-dsa-error]").forEach((p) => { p.textContent = ""; p.hidden = true; });
    setHidden(el(panel, "[data-dsa-failed]"), true);
    log.textContent = "";
    detail.open = true;

    // El cuerpo se arma ahora y el secreto se borra del campo YA: no queda en el DOM durante la corrida ni para reintentar.
    const body = new URLSearchParams(new FormData(form));
    form.querySelectorAll('input[type="password"]').forEach((i) => { i.value = ""; });

    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    setHidden(progress, false);
    status.textContent = status.dataset.runningText;

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
          } else if (event.t === "done") {
            done = event;
          }
        }
      }
    } catch (error) {
      done = { state: "FAILED", error_code: "CONNECTION_INTERRUPTED", title: "No se pudo completar la extracción", message: "Se interrumpió la conexión con el servidor. Revisá el detalle técnico e intentá nuevamente.", errors: {} };
    }

    if (done && done.state === "SUCCESS" && done.result_url) {
      window.location.assign(done.result_url);                // el informe se abre solo; el botón queda deshabilitado hasta salir
      return;
    }

    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    status.textContent = "";
    if (!done) return;

    if (done.state === "INVALID") {
      Object.entries(done.errors || {}).forEach(([field, message]) => {
        const target = form.querySelector('[data-dsa-error="' + field + '"]');
        if (target) { target.textContent = message; target.hidden = false; }
      });
      setHidden(progress, true);
    } else {
      el(panel, "[data-dsa-title]").textContent = done.title || "";
      el(panel, "[data-dsa-message]").textContent = done.message || "";
      el(panel, "[data-dsa-code]").textContent = done.error_code || "";
      setHidden(el(panel, "[data-dsa-failed]"), false);
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-dsa-form")) return;
    event.preventDefault();                                   // jamás navegación de formulario clásica
    const submit = form.querySelector("[data-dsa-submit]");
    if (submit && submit.disabled) return;                    // sin doble ejecución por clicks repetidos
    run(form);
  });
})();

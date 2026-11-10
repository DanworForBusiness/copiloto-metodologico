// "Assessment de tu modelo" — ejecución desde SAP Datasphere con avance LOCAL en tiempo real y apertura automática del informe.
//
// Por qué NO usa HTMX: la ejecución es técnica y determinista, no una respuesta de IA. Tiene su propio panel y su propio estado y no
// comparte ningún indicador ni evento global con las demás interacciones del panel lateral.
//
// Contrato (ver webapp/routers/datasphere_assessment.py): POST (cuerpo urlencoded, jamás query string) → respuesta NDJSON:
//   {"t":"start","run_id"} · {"t":"line","v":"<línea del extractor>"}* · {"t":"phase","v":"<hito>"}* · {"t":"ping"}* ·
//   {"t":"done","state","error_code","message","title","errors","result_url"}
// Todo texto externo entra con textContent (nunca como HTML). El navegador no interpreta ni re-diagnostica: muestra lo que el
// backend emite. El Client Secret se lee del formulario, se limpia del campo ANTES de enviar y no se guarda en ningún lado.
// Éxito = el backend entrega `result_url` y el navegador abre el informe automáticamente.
// La extracción es una operación del SERVIDOR: si la conexión se corta, se retoma con GET …/operation/{run_id}?since=N (sin reiniciarla).
(function () {
  "use strict";

  // Fases semánticas del backend en las que hay trabajo en curso ("extracting" es la fase inicial, antes de cualquier evento).
  const WORKING = new Set(["extracting", "assessing"]);
  // Recuperación tras un corte de la conexión: consulta la operación del servidor cada RECOVERY_POLL_MS; se rinde tras
  // RECOVERY_MAX_FAILURES consultas fallidas SEGUIDAS (red caída). Una extracción larga no tiene límite propio.
  const RECOVERY_POLL_MS = 3000;
  const RECOVERY_MAX_FAILURES = 20;

  function el(root, sel) { return root.querySelector(sel); }

  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }

  async function run(form) {
    const panel = form.closest("#dsa-panel");
    const progress = el(panel, "[data-dsa-progress]");
    const status = el(panel, "[data-dsa-status]");
    const spinner = el(panel, "[data-dsa-spinner]");
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
    setHidden(spinner, false);                                 // extracción en curso: misma fila de procesamiento, spinner visible

    let done = null;
    let runId = null;
    function handle(event) {
      if (event.t === "start") {
        runId = event.run_id || null;                         // cómo retomar la operación del servidor si esta conexión se corta
        if (window.dwCopilotOps) window.dwCopilotOps.attach(runId);   // el aviso global no la duplica mientras este panel la sigue
      } else if (event.t === "line") {
        log.textContent += event.v + "\n";
        log.scrollTop = log.scrollHeight;
      } else if (event.t === "phase") {
        status.textContent = event.v;                          // hito veraz emitido por el backend (sin porcentajes)
        setHidden(spinner, !WORKING.has(event.phase));       // presentación: el spinner solo gira en fases con trabajo en curso
      } else if (event.t === "done") {
        done = event;
        if (window.dwCopilotOps) window.dwCopilotOps.seen(runId);     // la vio terminar acá: el aviso global no la repite
      }                                                        // "ping": latido del servidor, sin efecto visible
    }
    let received = 0;                                          // eventos de la operación ya mostrados (= cursor del servidor; start y ping no cuentan)
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
          if (event.t !== "start" && event.t !== "ping") received += 1;
          handle(event);
        }
      }
    } catch (error) {
      done = null;                                             // se intenta recuperar la operación antes de declarar la falla
    }

    // La conexión se cortó (proxy, red) pero la extracción sigue en el servidor: se retoma la MISMA operación sin reiniciarla.
    if (!done && runId) {
      const url = form.getAttribute("action").replace(/\/run$/, "/operation/") + encodeURIComponent(runId);
      status.textContent = status.dataset.resumingText || "La conexión se interrumpió; seguimos la extracción en curso...";
      setHidden(spinner, false);
      let failures = 0;
      while (!done && failures < RECOVERY_MAX_FAILURES) {
        await new Promise((resolve) => setTimeout(resolve, RECOVERY_POLL_MS));
        try {
          const response = await fetch(url + "?since=" + received, { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } });
          if (response.status === 404) break;                  // vencida o ajena: no hay nada que retomar
          if (!response.ok) throw new Error("HTTP " + response.status);
          const data = await response.json();
          failures = 0;
          (data.events || []).forEach(handle);
          received = data.next;
        } catch (error) {
          failures += 1;
        }
      }
    }
    if (!done) {
      done = { state: "FAILED", error_code: "CONNECTION_INTERRUPTED", title: "No se pudo completar la extracción", message: "Se interrumpió la conexión con el servidor. Si la extracción terminó, su resultado aparece en «Assessments recientes». Revisá el detalle técnico e intentá nuevamente.", errors: {} };
    }

    if (done && done.state === "SUCCESS" && done.result_url) {
      window.location.assign(done.result_url);                // el informe se abre solo; el botón queda deshabilitado hasta salir
      return;
    }

    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    status.textContent = "";
    setHidden(spinner, true);                                  // la falla detiene el estado de procesamiento
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
      if (done.error_code) log.textContent += "[" + done.error_code + "]" + String.fromCharCode(10);   // el código técnico vive en "Detalle técnico"
      setHidden(el(panel, "[data-dsa-failed]"), false);
    }
  }

  // Eliminar un assessment reciente (form[data-dsa-delete], confirmado por el diálogo compartido del Hub: la primera vez el submit llega con
  // defaultPrevented y se ignora; tras confirmar se reenvía y entra acá). Quita el ítem sin recargar; sin ítems, desaparece la sección.
  async function remove(form) {
    const item = form.closest("[data-dsa-recent-item]");
    const section = form.closest("[data-dsa-recent]");
    try {
      const response = await fetch(form.getAttribute("action"), {
        method: "POST", body: new URLSearchParams(new FormData(form)), credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" },
      });
      if (!response.ok) return;
    } catch (error) {
      return;
    }
    if (item) item.remove();
    if (section && !section.querySelector("[data-dsa-recent-item]")) section.remove();
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form instanceof HTMLFormElement && form.hasAttribute("data-dsa-delete")) {
      if (event.defaultPrevented) return;
      event.preventDefault();
      remove(form);
      return;
    }
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-dsa-form")) return;
    event.preventDefault();                                   // jamás navegación de formulario clásica
    const submit = form.querySelector("[data-dsa-submit]");
    if (submit && submit.disabled) return;                    // sin doble ejecución por clicks repetidos
    run(form);
  });
})();

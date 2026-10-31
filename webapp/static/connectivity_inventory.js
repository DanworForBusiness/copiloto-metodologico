// "Extraer conexiones" (F2.1, módulo 01) — extracción AD-HOC de SOLO LECTURA con avance local y resultado TEMPORAL.
// Misma mecánica NDJSON que datasphere_publication.js, con su PROPIO panel y su PROPIA ruta. A diferencia del módulo 04 no hay
// líneas del extractor: el backend solo emite fases y, al final, el resumen SANEADO (B1): estados, conteos y presencia de datos.
//
// Contrato (ver webapp/routers/connectivity_inventory.py): POST urlencoded → NDJSON:
//   {"t":"start"} · {"t":"phase","phase","v"} · {"t":"done","state","error_code","message","title","errors","summary"}
// Todo texto entra con textContent; las filas se crean con el DOM (nunca innerHTML). El Client Secret se limpia del campo ANTES de
// enviar. Nada se guarda: al recargar, el resultado desaparece.
(function () {
  "use strict";
  if (window.__dscInventoryReady) return;                    // el parcial puede cargarse más de una vez (pestañas/HTMX)
  window.__dscInventoryReady = true;

  const CHECK_LABELS = { oauth_token: "OAuth (token y login)", list_spaces: "Listar Spaces", list_connections: "Listar conexiones", read_detail: "Leer detalle" };
  const EXHAUSTIVENESS_HELP = {
    VERIFIED: "La fuente garantiza que el listado es completo.",
    UNVERIFIABLE: "El CLI no expone total ni paginación: no se puede probar que estén todas, aunque la ejecución haya sido completa.",
    INCOMPLETE: "La extracción no cubrió todo el alcance pedido.",
  };
  const PHYS = ["host", "sysid", "systemnumber", "client"];
  const AGENT = ["agent_name", "location"];

  function el(root, sel) { return root.querySelector(sel); }
  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }
  function row(tbody, cells, header) {
    const tr = document.createElement("tr");
    cells.forEach((text, i) => {
      const cell = document.createElement(header && i === 0 ? "th" : "td");
      cell.textContent = text;
      tr.appendChild(cell);
    });
    tbody.appendChild(tr);
  }
  function yesNo(v) { return v ? "Sí" : "—"; }
  function counts(obj) {
    const parts = Object.entries(obj || {}).map(([k, n]) => k + ": " + n);
    return parts.length ? parts.join(" · ") : "ninguno";
  }

  function renderSummary(panel, summary) {
    const overview = el(panel, "[data-dsc-overview]");
    const conns = el(panel, "[data-dsc-connections]");
    overview.textContent = "";
    conns.textContent = "";
    if (!summary) return false;
    Object.entries(summary.preflight || {}).forEach(([name, check]) => {
      row(overview, [CHECK_LABELS[name] || name, check.status + (check.code ? " (" + check.code + ")" : "")], true);
    });
    if (summary.abort) row(overview, ["Interrupción", summary.abort.code + " — " + summary.abort.text], true);
    const inv = summary.inventory;
    if (inv) {
      const cov = inv.coverage || {};
      row(overview, ["Estado de ejecución", inv.status], true);
      row(overview, ["Exhaustividad", inv.exhaustiveness + " — " + (EXHAUSTIVENESS_HELP[inv.exhaustiveness] || "")], true);
      row(overview, ["Spaces", "pedidos " + cov.expected_spaces + " · accesibles " + cov.accessible_spaces + " · inaccesibles " + cov.inaccessible_spaces], true);
      row(overview, ["Conexiones", "descubiertas " + cov.discovered_connections + " · extraídas " + cov.extracted_connections], true);
      row(overview, ["Errores", cov.errors_total + " (" + counts(cov.errors_by_code) + ")"], true);
      (inv.connections || []).forEach((c) => {
        row(conns, [c.ref, c.space_ref]
          .concat(PHYS.map((k) => yesNo((c.physical_present || {})[k])))
          .concat(AGENT.map((k) => yesNo((c.agent_present || {})[k]))));
      });
      setHidden(el(panel, "[data-dsc-conn-wrap]"), !(inv.connections || []).length);
    } else {
      setHidden(el(panel, "[data-dsc-conn-wrap]"), true);
    }
    return true;
  }

  async function run(form) {
    const panel = form.closest("#dsc-panel");
    const progress = el(panel, "[data-dsc-progress]");
    const status = el(panel, "[data-dsc-status]");
    const spinner = el(panel, "[data-dsc-spinner]");
    const submit = el(form, "[data-dsc-submit]");
    const failed = el(panel, "[data-dsc-failed]");
    const result = el(panel, "[data-dsc-result]");

    form.querySelectorAll("[data-dsc-error]").forEach((p) => { p.textContent = ""; p.hidden = true; });
    setHidden(failed, true);
    setHidden(result, true);

    const body = new URLSearchParams(new FormData(form));
    form.querySelectorAll('input[type="password"]').forEach((i) => { i.value = ""; });

    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    setHidden(progress, false);
    status.textContent = status.dataset.runningText;
    setHidden(spinner, false);

    let done = null;
    try {
      const response = await fetch(form.getAttribute("action"), {
        method: "POST", body, credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
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
          if (event.t === "phase") status.textContent = event.v;
          else if (event.t === "done") done = event;
        }
      }
    } catch (error) {
      done = { state: "FAILED", error_code: "CONNECTION_INTERRUPTED", title: "No se pudo extraer las conexiones", message: "Se interrumpió la conexión con el servidor. Intentá nuevamente.", errors: {} };
    }

    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    status.textContent = "";
    setHidden(spinner, true);
    if (!done) return;

    if (done.state === "INVALID") {
      Object.entries(done.errors || {}).forEach(([fieldName, message]) => {
        const target = form.querySelector('[data-dsc-error="' + fieldName + '"]');
        if (target) { target.textContent = message; target.hidden = false; }
      });
      setHidden(progress, true);
      return;
    }
    if (done.state !== "SUCCESS") {
      el(panel, "[data-dsc-title]").textContent = done.title || "";
      el(panel, "[data-dsc-message]").textContent = (done.message || "") + (done.error_code ? " [" + done.error_code + "]" : "");
      setHidden(failed, false);
    }
    setHidden(result, !renderSummary(panel, done.summary));
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-dsc-form")) return;
    event.preventDefault();
    const submit = form.querySelector("[data-dsc-submit]");
    if (submit && submit.disabled) return;
    run(form);
  });
})();

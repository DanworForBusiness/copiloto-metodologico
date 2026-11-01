// "Extraer conexiones" (F2.1, módulo 01) — extracción AD-HOC de SOLO LECTURA con avance local y resultado TEMPORAL.
// Misma mecánica NDJSON y mismo patrón visual que datasphere_publication.js (fila de estado + spinner por fase, «Detalle técnico»,
// error), con su PROPIO panel y su PROPIA ruta. A diferencia del módulo 04 no hay líneas crudas del extractor: las `line` las arma el
// servidor SANEADAS (ordinales S1…, conteos y códigos) y el resultado es el resumen B1 (estados, conteos y presencia de datos).
//
// Contrato (ver webapp/routers/connectivity_inventory.py): POST urlencoded → NDJSON:
//   {"t":"start"} · {"t":"phase","phase","v"}* · {"t":"line","v"}* · {"t":"done","state","error_code","message","title","errors","operation_id","summary"}
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
  // Hitos con trabajo en curso (el spinner solo gira en estos; mismo criterio `WORKING` que el módulo 04). Los finales
  // (completed | partial | failed) dejan su texto visible y el spinner quieto.
  const WORKING = new Set(["preflight", "listing", "reading", "summarizing"]);
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
    const preflight = el(panel, "[data-dsc-preflight]");
    const overview = el(panel, "[data-dsc-overview]");
    const conns = el(panel, "[data-dsc-connections]");
    preflight.textContent = "";
    overview.textContent = "";
    conns.textContent = "";
    if (!summary) return false;
    const checks = summary.preflight || {};
    // Mínimo para extraer (mismo criterio del servidor): OAuth y Listar conexiones en OK. Con eso, un «Listar Spaces» fallido se
    // muestra tal cual (estado y código) e identificado como no bloqueante para ESTA extracción.
    const authorized = (checks.oauth_token || {}).status === "OK" && (checks.list_connections || {}).status === "OK";
    Object.entries(checks).forEach(([name, check]) => {
      let text = check.status + (check.code ? " (" + check.code + ")" : "");
      if (name === "list_spaces" && authorized && check.status !== "OK") text += " — no bloquea la extracción";
      row(preflight, [CHECK_LABELS[name] || name, text], true);
    });
    setHidden(el(panel, "[data-dsc-preflight-wrap]"), !Object.keys(checks).length);
    if (summary.abort) row(overview, ["Interrupción", summary.abort.code + " — " + summary.abort.text], true);
    const inv = summary.inventory;
    if (inv) {
      const cov = inv.coverage || {};
      row(overview, ["Estado de ejecución", inv.status], true);
      row(overview, ["Exhaustividad", inv.exhaustiveness + " — " + (EXHAUSTIVENESS_HELP[inv.exhaustiveness] || "")], true);
      row(overview, ["Spaces", "pedidos " + cov.expected_spaces + " · accesibles " + cov.accessible_spaces + " · inaccesibles " + cov.inaccessible_spaces], true);
      row(overview, ["Conexiones", "descubiertas " + cov.discovered_connections + " · extraídas " + cov.extracted_connections], true);
      row(overview, ["Errores", cov.errors_total + " (" + counts(cov.errors_by_code) + ")"], true);
      if (summary.budget) {
        const b = summary.budget;
        row(overview, ["Presupuesto usado", "solicitudes " + b.requests_used + "/" + b.max_requests + " · reintentos " + b.retries_used + "/" +
          b.max_total_retries + " · " + b.elapsed_s + " s / " + b.max_duration_s + " s"], true);
      }
      (inv.connections || []).forEach((c) => {
        row(conns, [c.ref, c.space_ref]
          .concat(PHYS.map((k) => yesNo((c.physical_present || {})[k])))
          .concat(AGENT.map((k) => yesNo((c.agent_present || {})[k]))));
      });
      setHidden(el(panel, "[data-dsc-conn-wrap]"), !(inv.connections || []).length);
    } else {
      setHidden(el(panel, "[data-dsc-conn-wrap]"), true);
    }
    setHidden(el(panel, "[data-dsc-overview-wrap]"), !overview.children.length);   // sin filas (p. ej. OAuth fallido) no hay bloque vacío
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
    const log = el(panel, "[data-dsc-log]");
    const detail = el(panel, "[data-dsc-detail]");

    form.querySelectorAll("[data-dsc-error]").forEach((p) => { p.textContent = ""; p.hidden = true; });
    setHidden(failed, true);
    setHidden(result, true);
    log.textContent = "";
    detail.open = true;

    const body = new URLSearchParams(new FormData(form));
    form.querySelectorAll('input[type="password"]').forEach((i) => { i.value = ""; });

    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    setHidden(progress, false);
    status.textContent = status.dataset.runningText;
    setHidden(spinner, false);

    let done = null;
    let finalPhase = "";
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
          if (event.t === "line") {
            log.textContent += event.v + "\n";                     // actividad saneada del servidor (nunca líneas crudas)
            log.scrollTop = log.scrollHeight;
          } else if (event.t === "phase") {
            status.textContent = event.v;                          // hito realmente observado (sin porcentajes)
            setHidden(spinner, !WORKING.has(event.phase));
            finalPhase = WORKING.has(event.phase) ? "" : event.v;
          } else if (event.t === "done") {
            done = event;
          }
        }
      }
    } catch (error) {
      done = { state: "FAILED", error_code: "CONNECTION_INTERRUPTED", title: "No se pudo extraer las conexiones", message: "Se interrumpió la conexión con el servidor. Intentá nuevamente.", errors: {} };
    }

    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    status.textContent = finalPhase;                                // el hito final queda visible; sin él (corte), nada
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
    if (done.error_code) log.textContent += "[" + done.error_code + "]\n";        // el código técnico vive en «Detalle técnico» (como el 04)
    if (done.operation_id) log.textContent += "Operación: " + done.operation_id + "\n";
    if (done.state !== "SUCCESS") {
      el(panel, "[data-dsc-title]").textContent = done.title || "";
      el(panel, "[data-dsc-message]").textContent = done.message || "";
      setHidden(failed, false);
    }
    setHidden(result, !renderSummary(panel, done.summary));
  }

  // ── Filas dinámicas de Space (el environment no se pide: es gobierno, F2.2) ─────
  // Una fila al abrir; «+ Otro espacio» clona el <template> del servidor (única fuente de marcado) y «Quitar» elimina SOLO esa fila:
  // el resto de los nodos no se toca, así que sus valores se conservan. Siempre queda al menos una fila. El máximo lo fija el servidor.
  function spaceRows(form) { return form.querySelectorAll("[data-dsc-spaces] [data-dsc-space-row]"); }

  function syncAddButton(form) {
    const box = el(form, "[data-dsc-spaces]");
    const add = el(form, "[data-dsc-add-space]");
    if (!box || !add) return;
    const atMax = spaceRows(form).length >= Number(box.dataset.max || 0);
    add.disabled = atMax;
    setHidden(el(form, "[data-dsc-max-note]"), !atMax);   // el aviso del tope aparece SOLO al alcanzarlo
  }

  function addSpaceRow(form) {
    const box = el(form, "[data-dsc-spaces]");
    const tpl = el(form, "[data-dsc-space-template]");
    if (!box || !tpl || spaceRows(form).length >= Number(box.dataset.max || 0)) return;
    const next = Number(form.dataset.dscNextRow || spaceRows(form).length + 1);
    form.dataset.dscNextRow = String(next + 1);
    const row = tpl.content.firstElementChild.cloneNode(true);
    const input = row.querySelector("[data-dsc-space-input]");
    input.id = "dsc-space-" + next;
    row.querySelector("[data-dsc-space-label]").htmlFor = input.id;
    box.appendChild(row);
    syncAddButton(form);
    input.focus();
  }

  function removeSpaceRow(form, row) {
    if (spaceRows(form).length <= 1) return;                   // siempre al menos un grupo
    row.remove();
    syncAddButton(form);
  }

  function duplicatedSpace(form) {
    const seen = new Set();
    for (const input of form.querySelectorAll("[data-dsc-spaces] [data-dsc-space-input]")) {
      const key = input.value.trim().toLowerCase();
      if (!key) continue;
      if (seen.has(key)) return input;
      seen.add(key);
    }
    return null;
  }

  document.addEventListener("click", (event) => {
    const add = event.target.closest && event.target.closest("[data-dsc-add-space]");
    const remove = event.target.closest && event.target.closest("[data-dsc-remove-space]");
    const form = (add || remove) && (add || remove).closest("[data-dsc-form]");
    if (!form) return;
    event.preventDefault();
    if (add) addSpaceRow(form);
    else removeSpaceRow(form, remove.closest("[data-dsc-space-row]"));
  });

  document.addEventListener("input", (event) => {               // al corregir un Space, el aviso de filas deja de aplicar
    if (!event.target.matches || !event.target.matches("[data-dsc-space-input]")) return;
    const errorBox = event.target.closest("[data-dsc-form]").querySelector('[data-dsc-error="spaces"]');
    if (errorBox) { errorBox.textContent = ""; errorBox.hidden = true; }
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-dsc-form")) return;
    event.preventDefault();
    const submit = form.querySelector("[data-dsc-submit]");
    if (submit && submit.disabled) return;
    const errorBox = el(form, '[data-dsc-error="spaces"]');
    const dup = duplicatedSpace(form);
    if (dup) {                                                   // el servidor también lo rechaza; acá se evita el viaje
      errorBox.textContent = "Hay Spaces repetidos.";
      errorBox.hidden = false;
      dup.focus();
      return;
    }
    run(form);
  });
})();

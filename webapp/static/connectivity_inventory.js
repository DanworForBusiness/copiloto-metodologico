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
    log.textContent = status.dataset.runningText + "\n";        // «Detalle técnico» arranca con el estado de inicio (no vacío)
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
            log.textContent += "» " + event.v + "\n";              // el mismo hito queda en «Detalle técnico», en el orden en que el servidor lo observó
            log.scrollTop = log.scrollHeight;
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
    renderCapture(panel, done.capture, done.operation_id);
    if (done.catalog_review && window.htmx) {                       // comparación + assessment F1 en el panel central (mismo mecanismo del Catálogo)
      const stem = (form.getAttribute("action").match(/\/modules\/([^/]+)\//) || [])[1];
      window.htmx.ajax("GET", "/modules/" + stem + "/connectivity-inventory/review/" + encodeURIComponent(done.operation_id),
                       { target: "#catalog-detail-slot", swap: "innerHTML" });
    }
  }

  async function submitReview(form) {
    const view = form.closest("[data-conn-review]");
    const error = el(form, "[data-review-error]");
    const button = el(form, "[data-review-submit]");
    setHidden(error, true);
    const selected = Array.from(form.elements).filter((i) => i.name === "selected" && i.checked).map((i) => i.value);   // casillas de la tabla (atributo `form`)
    if (!selected.length) { error.textContent = "Elegí al menos una conexión."; setHidden(error, false); return; }
    const ok = await dwConfirm({ title: "Publicar en el Catálogo", confirmLabel: "Publicar",
      message: "Se crean o actualizan " + selected.length + " conexión(es) del Catálogo. No se borra ninguna y se conserva lo cargado a mano." });
    if (!ok) return;
    const body = new URLSearchParams(new FormData(form));
    body.set("operation_id", view.dataset.operationId);
    button.disabled = true;
    try {
      const response = await fetch(view.dataset.approveUrl, { method: "POST", body, credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.status !== "PUBLISHED") {
        error.textContent = data.detail || "No se pudo publicar."; setHidden(error, false); return;
      }
      setHidden(form, true);
      Array.from(form.elements).forEach((i) => { if (i.name === "selected") i.disabled = true; });
      const done = el(view, "[data-review-done]");
      const n = data.published.length;
      el(done, "[data-review-done-msg]").textContent = (n === 1 ? "Se incorporó 1 conexión" : "Se incorporaron " + n + " conexiones") + " al Catálogo." +
        (data.durability === "remote" ? " Persistencia remota confirmada." : "");
      setHidden(el(done, "[data-review-done-warn]"), data.durability === "remote");   // aviso aparte, no un fallo
      setHidden(done, false);
    } catch (_e) {
      error.textContent = "Error de red: recargá antes de reintentar."; setHidden(error, false);
    } finally {
      button.disabled = false;
    }
  }

  // ── F2.2c: captura gobernada OPT-IN (solo llega `done.capture` para el dueño) ─────
  function newRequestId() {
    return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "cap-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }
  function renderCapture(panel, cap, operationId) {
    const box = el(panel, "[data-dsc-capture]");
    if (!box) return;
    setHidden(box, !cap);
    if (!cap) return;
    const form = el(box, "[data-dsc-capture-form]");
    setHidden(form, false);
    setHidden(el(box, "[data-dsc-capture-done]"), true);
    setHidden(el(box, "[data-dsc-capture-error]"), true);
    form.elements.operation_id.value = operationId;
    form.elements.client.value = cap.client;
    form.elements.request_id.value = newRequestId();             // un pedido = un id: el doble envío es idempotente
    form.elements.rationale.value = "";
    const select = el(form, "[data-dsc-capture-tenant]");
    select.textContent = "";
    const first = document.createElement("option");
    first.value = ""; first.textContent = cap.tenants.length ? "Elegí el tenant..." : "Sin tenants activos para este cliente";
    select.appendChild(first);
    cap.tenants.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.tenant_id; o.textContent = t.tenant_id + (t.display_name ? " · " + t.display_name : "");
      if (t.tenant_id === cap.suggested) o.selected = true;
      select.appendChild(o);
    });
    el(box, "[data-dsc-capture-summary]").textContent = cap.connections + " conexiones en " + cap.spaces + (cap.spaces === 1 ? " Space. " : " Spaces. ") +
      (cap.suggested ? "Tenant sugerido por el host vinculado: " + cap.suggested + "." :
       "El host de esta extracción no está vinculado a ningún tenant: vinculalo en «Tenants y ambientes» antes de capturar.");
    // Vencimiento de la retención temporal (lo fija el servidor): pasado ese plazo, la extracción hay que ejecutarla de nuevo.
    const minutes = Math.round((cap.expires_in_s || 0) / 60);
    const until = new Date(Date.now() + (cap.expires_in_s || 0) * 1000).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
    el(box, "[data-dsc-capture-expiry]").textContent = "Disponible para capturar durante " + minutes + " minutos (hasta las " + until +
      "). Después vence y hay que volver a extraer.";
  }
  async function submitCapture(form) {
    const box = form.closest("[data-dsc-capture]");
    const error = el(box, "[data-dsc-capture-error]");
    const button = el(form, "[data-dsc-capture-submit]");
    setHidden(error, true);
    button.disabled = true;
    try {
      const response = await fetch("/connectivity-registry/capture", {
        method: "POST", body: new URLSearchParams(new FormData(form)), credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.status !== "APPLIED") {
        el(error, "[data-dsc-capture-error-title]").textContent = "Captura rechazada (" + response.status + ")";
        el(error, "[data-dsc-capture-error-msg]").textContent = data.detail || "No se pudo capturar el inventario.";
        setHidden(error, false);
        return;
      }
      setHidden(form, true);
      const done = el(box, "[data-dsc-capture-done]");
      done.textContent = "✓ Inventario capturado para " + data.tenant_id + ": " + data.connections + " conexiones; " + data.governed_spaces +
        " Space(s) con ambiente gobernado y " + data.ungoverned_spaces + " sin ambiente (quedan fuera del alcance de la reconciliación). " +
        "La reconciliación está en «Tenants y ambientes».";
      setHidden(done, false);
    } catch (_e) {
      el(error, "[data-dsc-capture-error-title]").textContent = "Error de red";
      el(error, "[data-dsc-capture-error-msg]").textContent = "No se pudo contactar al servidor. Reintentá: el mismo pedido no se duplica.";
      setHidden(error, false);
    } finally {
      button.disabled = false;
    }
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
    if (form instanceof HTMLFormElement && form.hasAttribute("data-review-form")) {
      event.preventDefault();
      submitReview(form);
      return;
    }
    if (form instanceof HTMLFormElement && form.hasAttribute("data-dsc-capture-form")) {
      event.preventDefault();
      submitCapture(form);
      return;
    }
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

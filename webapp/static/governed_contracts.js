/* Contratos Gobernados (B4) — acciones owner-only Ratificar/Revocar.
   Ejecuta contra las rutas POST de B3 (/governed-decisions/ratify|revoke); la autorización REAL la impone el backend
   (session -> same-origin -> APP_OWNER). Nunca escribe en silencio: confirma, muestra progreso, resultado y errores.
   request_id NUEVO por cada decisión intencional (un click = una decisión). Delegación de eventos: sobrevive a los swaps HTMX. */
(function () {
  "use strict";

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "gc-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  function el(root, sel) { return root ? root.querySelector(sel) : null; }

  async function reloadDetail(section) {
    const client = section.dataset.client;
    const rc = section.dataset.remoteConnection || "";
    const re = section.dataset.remoteEntity;
    const url = "/governed-contracts/detail?client=" + encodeURIComponent(client) +
      "&remote_connection=" + encodeURIComponent(rc) + "&remote_entity=" + encodeURIComponent(re);
    const panel = document.getElementById("governed-contracts-panel");
    try {
      const r = await fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "HX-Request": "true" } });
      if (r.ok && panel) panel.innerHTML = await r.text();
    } catch (_e) { /* el estado ya quedó registrado; el usuario puede recargar */ }
  }

  async function submit(section, kind) {
    const rationaleEl = el(section, "[data-gc-rationale]");
    const rationale = (rationaleEl && rationaleEl.value || "").trim();
    const errBox = el(section, "[data-gc-error]");
    const errTitle = el(section, "[data-gc-error-title]");
    const errMsg = el(section, "[data-gc-error-msg]");
    const progress = el(section, "[data-gc-progress]");
    const statusEl = el(section, "[data-gc-status]");
    const showError = (title, msg) => {
      if (errTitle) errTitle.textContent = title;
      if (errMsg) errMsg.textContent = msg || "";
      if (errBox) errBox.hidden = false;
    };
    if (errBox) errBox.hidden = true;

    if (!rationale) { showError("Justificación obligatoria", "Ingresá el motivo de la decisión."); return; }
    const verb = kind === "ratify" ? "RATIFICAR" : "REVOCAR";
    const ident = (section.dataset.remoteConnection || "—") + " · " + section.dataset.remoteEntity;
    if (!window.confirm(verb + " el contrato\n" + ident + "\n\nQueda registrado de forma permanente. ¿Confirmás?")) return;

    const body = new URLSearchParams();
    body.set("client", section.dataset.client);
    body.set("remote_connection", section.dataset.remoteConnection || "");
    body.set("remote_entity", section.dataset.remoteEntity);
    body.set("rationale", rationale);
    body.set("request_id", uuid());   // nueva decisión intencional => nuevo request_id

    if (progress) progress.hidden = false;
    if (statusEl) statusEl.textContent = (kind === "ratify" ? "Ratificando" : "Revocando") + " contrato…";
    try {
      const r = await fetch("/governed-decisions/" + kind, {
        method: "POST", body, credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      if (progress) progress.hidden = true;
      if (!r.ok) {
        let detail = "No se pudo completar la operación.";
        try { detail = (await r.json()).detail || detail; } catch (_e) {}
        showError("Operación rechazada (" + r.status + ")", detail);
        return;
      }
      await reloadDetail(section);   // refleja el estado actualizado + historial
    } catch (_e) {
      if (progress) progress.hidden = true;
      showError("Error de red", "No se pudo contactar al servidor; no se registró ninguna decisión.");
    }
  }

  document.addEventListener("click", function (ev) {
    const rat = ev.target.closest("[data-gc-ratify]");
    const rev = ev.target.closest("[data-gc-revoke]");
    if (!rat && !rev) return;
    const section = ev.target.closest("[data-gc-actions]");
    if (!section) return;
    ev.preventDefault();
    submit(section, rat ? "ratify" : "revoke");
  });
})();

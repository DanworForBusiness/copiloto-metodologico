/* Resoluciones de revisión (Iteración III) — acción icónica compacta + diálogo CONTEXTUAL nativo del Hub (dw-confirm busqueda-dialog).
   Un único control por ocurrencia ([data-rr-open], payload JSON en data-rr) abre el diálogo compartido #rr-dialog en uno de tres modos:
     resolve    (Sin resolver / Reabierta) -> hallazgo + artefacto + contexto técnico; luego disposición + justificación -> /review-resolutions/resolve
     consult    (decisión vigente)          -> decisión, actor, fecha, fundamento, vigencia y evidencia; luego hallazgo; Revocar en dos pasos -> /review-resolutions/revoke
     reconcile  (En conflicto)              -> decisiones concurrentes; hallazgo; disposición + justificación -> /review-resolutions/reconcile (supersede todas las ramas)
   Autorización REAL en el backend (sesión -> same-origin -> APP_OWNER); el actor sale de la sesión. Append-only, nunca en silencio: estados de
   guardado/errores visibles y recarga del informe. Todo el contenido dinámico entra con textContent. Delegación a nivel document (sobrevive a swaps HTMX). */
(function () {
  "use strict";
  if (window.__rrWired) return;
  window.__rrWired = true;

  const DISPOSITION_HINTS = {
    VALIDATED_DESIGN: "Cierra la revisión: el diseño es correcto para este caso.",
    NOT_APPLICABLE: "Cierra la revisión: la regla no aplica a este caso.",
    CORRECTION_REQUIRED: "Deja la revisión pendiente de remediación hasta corregir el modelo.",
    DEFERRED: "Posterga la revisión: queda abierta y con fecha de revisión.",
    ACCEPTED: "Cierra la oportunidad: se adopta la optimización propuesta.",
    DISCARDED: "Cierra la oportunidad: se descarta para este caso.",
  };
  const TONES = ["plena", "buena", "parcial", "neutra", "pendiente"];

  function uuid() { return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "rr-" + Date.now() + "-" + Math.random().toString(16).slice(2); }
  function dlg() { return document.getElementById("rr-dialog"); }
  function q(sel) { const d = dlg(); return d ? d.querySelector(sel) : null; }
  // `.asr-generate` y otros primitivos fijan `display` y pisan [hidden]: los botones se muestran/ocultan por style.display.
  function showBtn(btn, on) { if (btn) btn.style.display = on ? "" : "none"; }
  // Ídem para bloques: `.hc-profile-filters` (flex) y `.dw-confirm__select` fijan display; [hidden] + style.display cubren ambos casos.
  function show(el, on) { if (el) { el.hidden = !on; el.style.display = on ? "" : "none"; } }

  function badge(el, label, tone) {
    if (!el) return;
    TONES.forEach(function (t) { el.classList.remove("catalog-card__verdict--" + t); });
    // `.catalog-card__verdict` fija display y pisa [hidden]: se oculta por style.display (mismo criterio que showBtn).
    if (!label || label === "—") { el.hidden = true; el.style.display = "none"; return; }
    el.textContent = label; el.classList.add("catalog-card__verdict--" + (tone || "neutra")); el.hidden = false; el.style.display = "";
  }
  function kvRows(tbody, rows) {
    tbody.textContent = "";
    rows.forEach(function (r) {
      if (!r[1]) return;
      const tr = document.createElement("tr"), th = document.createElement("th"), td = document.createElement("td");
      // `td.left` = celda de texto del primitivo `.dashboard-table`; su nowrap+elipsis está pensado para rótulos cortos: acá el valor (fundamento, contexto)
      // se lee completo. Se resuelve en línea y NO en tools.css (esa zona de la hoja está tomada por el stash de publicación protegido).
      th.className = "left"; th.scope = "row"; th.textContent = r[0]; td.className = "left"; td.style.whiteSpace = "normal"; td.textContent = r[1];
      tr.appendChild(th); tr.appendChild(td); tbody.appendChild(tr);
    });
  }
  function setBusy(btn, on) {
    const d = dlg(); if (!d) return;
    if (btn) btn.classList.toggle("htmx-request", !!on);
    d.querySelectorAll("button").forEach(function (b) { b.disabled = !!on; });
    d.setAttribute("aria-busy", on ? "true" : "false");
  }
  function showErr(msg) { const e = q("[data-rr-dlg-error]"); if (e) { e.textContent = msg; e.hidden = false; } }
  function clearErr() { const e = q("[data-rr-dlg-error]"); if (e) { e.textContent = ""; e.hidden = true; } }

  function askRationale(label, placeholder) {
    show(q("[data-rr-dlg-rationale-field]"), true); show(q("[data-rr-dlg-append-note]"), true);
    q("[data-rr-dlg-rationale-label]").textContent = label;
    const rat = q("[data-rr-dlg-rationale]"); rat.placeholder = placeholder; return rat;
  }

  function fillDispositions(allowed) {
    const sel = q("[data-rr-dlg-disposition]"); sel.textContent = "";
    const ph = document.createElement("option"); ph.value = ""; ph.textContent = "Elegí una disposición…"; sel.appendChild(ph);
    (allowed || []).forEach(function (p) { const o = document.createElement("option"); o.value = p[0]; o.textContent = p[1]; sel.appendChild(o); });
    sel.value = ""; q("[data-rr-dlg-disposition-hint]").textContent = "";
    show(q("[data-rr-dlg-disposition-field]"), true);
  }

  async function reload() {
    const d = dlg(); const url = d && d.dataset.reportUrl;
    const slot = document.getElementById("catalog-detail-slot");
    if (!url || !slot) return;
    const openId = (d.dataset.groupId || "");
    try {
      const r = await fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "HX-Request": "true" } });
      if (r.ok) {
        slot.innerHTML = await r.text(); if (window.htmx) htmx.process(slot);
        const g = openId && document.getElementById(openId);           // re-abre el hallazgo para que se vea el estado persistido
        if (g && g.tagName === "DETAILS") { g.open = true; g.scrollIntoView({ block: "start" }); }
      }
    } catch (_e) { /* la decisión ya quedó registrada; recargar manualmente */ }
  }

  function open(opener) {
    const d = dlg(); if (!d || typeof d.showModal !== "function") return;
    let p = {}; try { p = JSON.parse(opener.dataset.rr || "{}"); } catch (_e) { return; }
    const isOpp = p.klass === "CONTEXTUAL_OPPORTUNITY", noun = isOpp ? "oportunidad" : (p.noun || "revisión");
    const mode = (p.state === "OPEN" || p.state === "REOPENED") ? "resolve" : (p.state === "CONTESTED" ? "reconcile" : "consult");
    const grp = opener.closest("details.dashboard-section");
    Object.assign(d.dataset, { tid: p.tid, flow: opener.dataset.rrFlow, client: opener.dataset.rrClient, reportUrl: opener.dataset.rrReportUrl,
      active: p.active || "", contested: (p.contested || []).map(function (c) { return c.event_id; }).join(","), mode: mode,
      groupId: grp ? grp.id : "", hints: JSON.stringify(p.hints || {}) });

    // reset
    clearErr(); setBusy(null, false);
    ["[data-rr-dlg-decision]", "[data-rr-dlg-contested]", "[data-rr-dlg-disposition-field]", "[data-rr-dlg-rationale-field]", "[data-rr-dlg-append-note]"]
      .forEach(function (s) { show(q(s), false); });
    q("[data-rr-dlg-rationale]").value = "";
    const submitBtn = q("[data-rr-dlg-submit]"), revokeBtn = q("[data-rr-dlg-revoke]");
    revokeBtn.dataset.step = "offer"; q("[data-rr-dlg-revoke-label]").textContent = "Revocar decisión";
    q("[data-rr-dlg-cancel]").textContent = mode === "consult" ? "Cerrar" : "Cancelar";

    // cabecera: tipo de caso + estado (planos que coexisten: estado principal, evidencia, vigencia)
    q("[data-rr-dlg-kicker]").textContent = p.finding || "";       // MISMO título que el contenedor del hallazgo, sin prefijos
    badge(q("[data-rr-dlg-state]"), p.state_label, p.state_tone);
    badge(q("[data-rr-dlg-evidence]"), mode === "consult" ? p.evidence_label : null, p.evidence_tone);
    badge(q("[data-rr-dlg-review]"), mode === "consult" && p.review_label !== "Vigente" ? p.review_label : null, p.review_tone);
    q("[data-rr-dlg-state-note]").textContent = p.state_note || "";

    // hallazgo (siempre presente; en resolución va primero porque no hay decisión previa)
    kvRows(q("[data-rr-dlg-finding-rows]"), [["Artefacto", p.artifact], ["Contexto técnico", p.context], ["Por qué aparece", p.explanation]]);

    if (mode === "resolve") {
      q("[data-rr-dlg-title]").textContent = "Resolver " + noun;
      fillDispositions(p.allowed);
      askRationale("Justificación", "Motivo de la decisión (obligatorio)");
      showBtn(submitBtn, true); q("[data-rr-dlg-submit-label]").textContent = "Registrar decisión"; submitBtn.dataset.mode = "resolve";
      showBtn(revokeBtn, false);
    } else if (mode === "reconcile") {
      q("[data-rr-dlg-title]").textContent = "Reconciliar " + noun + " en conflicto";
      const tb = q("[data-rr-dlg-contested-rows]"); tb.textContent = "";
      (p.contested || []).forEach(function (c) {
        const tr = document.createElement("tr");
        [c.disposition_label || c.disposition, c.actor, c.decided_at, c.rationale].forEach(function (v) { const td = document.createElement("td"); td.className = "left"; td.style.whiteSpace = "normal"; td.textContent = v || "—"; tr.appendChild(td); });
        tb.appendChild(tr);
      });
      show(q("[data-rr-dlg-contested]"), true);
      fillDispositions(p.allowed);
      askRationale("Justificación", "Motivo de la reconciliación (obligatorio)");
      showBtn(submitBtn, true); q("[data-rr-dlg-submit-label]").textContent = "Reconciliar"; submitBtn.dataset.mode = "reconcile";
      showBtn(revokeBtn, false);
    } else {
      q("[data-rr-dlg-title]").textContent = "Decisión sobre la " + noun;
      const dec = p.decision || {};
      kvRows(q("[data-rr-dlg-decision-rows]"), [
        ["Decisión", dec.disposition_label || dec.disposition], ["Registrada por", dec.actor], ["Fecha", dec.decided_at],
        ["Fundamento", dec.rationale], ["Vigencia", p.review_by ? ("Revisar antes del " + p.review_by + (p.review_label && p.review_label !== "Vigente" ? " · " + p.review_label : "")) : null],
        ["Evidencia", p.evidence_label],
      ]);
      show(q("[data-rr-dlg-decision]"), true);
      showBtn(submitBtn, false);
      showBtn(revokeBtn, !!p.active);            // revocar solo existe con una decisión vigente identificable
    }
    d.showModal();
    const first = mode === "consult" ? q("[data-rr-dlg-cancel]") : q("[data-rr-dlg-disposition]");
    if (first) first.focus({ preventScroll: true });
    const picker = d.querySelector(".busqueda-picker"); if (picker) picker.scrollTop = 0;   // el contexto se lee primero
  }

  async function submit(kind) {
    const d = dlg(); const ds = d.dataset;
    const rat = q("[data-rr-dlg-rationale]"), sel = q("[data-rr-dlg-disposition]");
    const rationale = (rat.value || "").trim(), disposition = sel ? sel.value : "";
    clearErr();
    if (kind !== "revoke" && !disposition) { showErr("Elegí una disposición."); sel.focus(); return; }
    if (!rationale) { showErr(kind === "revoke" ? "El motivo de la revocación es obligatorio." : "La justificación es obligatoria."); rat.focus(); return; }
    const btn = kind === "revoke" ? q("[data-rr-dlg-revoke]") : q("[data-rr-dlg-submit]");
    const body = new URLSearchParams();
    body.set("flow", ds.flow); body.set("client", ds.client); body.set("technical_id", ds.tid);
    body.set("rationale", rationale); body.set("request_id", uuid());
    if (kind === "resolve") { body.set("disposition", disposition); }
    else if (kind === "reconcile") { body.set("disposition", disposition); body.set("supersedes", ds.contested || ""); }
    else { body.set("revokes_event_id", ds.active || ""); }
    setBusy(btn, true);
    try {
      const r = await fetch("/review-resolutions/" + kind, { method: "POST", body, credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" } });
      if (!r.ok) {
        setBusy(btn, false);
        let det = "No se pudo completar la operación."; try { det = (await r.json()).detail || det; } catch (_e) {}
        showErr("No se registró la decisión (" + r.status + "): " + det); return;
      }
      setBusy(btn, false);
      if (d.open) d.close();
      await reload();
    } catch (_e) { setBusy(btn, false); showErr("Error de red: no se registró ninguna decisión."); }
  }

  document.addEventListener("click", function (ev) {
    const t = ev.target; if (!t.closest) return;
    const opener = t.closest("[data-rr-open]");
    if (opener) { ev.preventDefault(); open(opener); return; }
    const sub = t.closest("[data-rr-dlg-submit]");
    if (sub) { ev.preventDefault(); submit(sub.dataset.mode || "resolve"); return; }
    const rev = t.closest("[data-rr-dlg-revoke]");
    if (rev) {
      ev.preventDefault();
      if (rev.dataset.step === "offer") {            // paso 1: pedir el motivo; paso 2: confirmar
        rev.dataset.step = "confirm"; q("[data-rr-dlg-revoke-label]").textContent = "Confirmar revocación";
        q("[data-rr-dlg-cancel]").textContent = "Cancelar";
        clearErr();
        askRationale("Motivo de la revocación", "Por qué se revoca (obligatorio). La revisión vuelve a quedar sin resolver.").focus();
        return;
      }
      submit("revoke");
    }
  });
  document.addEventListener("change", function (ev) {
    if (!ev.target.matches || !ev.target.matches("[data-rr-dlg-disposition]")) return;
    const d = dlg(); let hints = {}; try { hints = JSON.parse(d.dataset.hints || "{}"); } catch (_e) {}
    q("[data-rr-dlg-disposition-hint]").textContent = hints[ev.target.value] || DISPOSITION_HINTS[ev.target.value] || "";   // ayuda contextual de la regla, si la hay
  });
})();

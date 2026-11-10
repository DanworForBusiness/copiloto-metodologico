// Aviso global de operaciones del Copiloto (diagnóstico ad hoc y publicación del Catálogo) + seguimiento en vivo de «Ver ejecución».
//
// Única fuente de estados: el registro del servidor (GET /copilot/operations, acotado al usuario). El navegador NO guarda run_ids para
// recuperar nada: al entrar a cualquier página del Copiloto se listan las operaciones propias. Solo guarda (por comodidad, en este
// navegador) qué avisos se cerraron. Cerrar el aviso NO interrumpe nada; este script no ejecuta, no reintenta ni cancela operaciones.
// Todo texto del servidor entra con textContent (nunca como HTML). Visual: el banner de Novedades + el spinner de «procesando» del Hub.
(function () {
  "use strict";

  const POLL_MS = 5000;             // solo mientras haya algo en curso; sin operaciones activas no se consulta más
  const LIVE_POLL_MS = 3000;
  const MAX_SHOWN = 3;
  const DISMISSED_KEY = "dw-copilot-ops-dismissed";
  const attached = new Set();       // operaciones que su PROPIO panel sigue en esta página (el aviso no las duplica)

  function readDismissed() {
    try { return new Set(JSON.parse(localStorage.getItem(DISMISSED_KEY) || "[]")); } catch (e) { return new Set(); }
  }
  function dismiss(runId) {
    const set = readDismissed();
    set.add(runId);
    try { localStorage.setItem(DISMISSED_KEY, JSON.stringify(Array.from(set).slice(-200))); } catch (e) { /* sin storage: el aviso igual se quita */ }
  }

  // API mínima para los paneles de las herramientas: «esta operación la sigo yo acá» y «ya la vio terminar acá».
  window.dwCopilotOps = {
    attach(runId) { if (runId) attached.add(runId); },
    seen(runId) { if (runId) dismiss(runId); },
  };

  function node(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function link(href, text) {
    const a = node("a", "whatsnew-banner__cta", text);
    a.href = href;
    return a;
  }

  function subject(op) {
    return [op.client, op.root_object].filter(Boolean).join(" · ");
  }

  function banner(op, onClose) {
    const box = node("div", "whatsnew-banner copilot-op");
    box.setAttribute("role", "status");
    box.dataset.runId = op.run_id;
    box.dataset.state = op.state;
    const msg = node("span", "whatsnew-banner__msg");
    if (op.state === "RUNNING") {
      msg.appendChild(node("span", "section-analysis-spinner"));
    } else {
      msg.appendChild(node("span", "whatsnew-banner__mark", op.state === "SUCCESS" ? "✓" : "!")).setAttribute("aria-hidden", "true");
    }
    const text = node("span");
    const head = op.state === "RUNNING" ? op.kind_label + " en curso" : op.state === "SUCCESS" ? op.kind_label + " completada" : op.kind_label + ": " + (op.title || op.state_label);
    text.appendChild(document.createTextNode(head + (subject(op) ? " · " + subject(op) : "") + (op.state === "RUNNING" ? ". Podés seguir navegando. " : ". ")));
    if (op.state === "SUCCESS" && op.result_url) {
      text.appendChild(link(op.result_url, op.kind === "publication" ? "Abrir el Catálogo" : "Abrir assessment"));
      text.appendChild(document.createTextNode(" · "));
    }
    text.appendChild(link(op.log_url, op.state === "RUNNING" || op.state === "SUCCESS" ? "Ver ejecución" : "Ver detalle"));
    msg.appendChild(text);
    box.appendChild(msg);
    const close = node("button", "whatsnew-banner__close", "✕");
    close.type = "button";
    close.title = "Cerrar (no interrumpe la ejecución)";
    close.setAttribute("aria-label", "Cerrar aviso de ejecución");
    close.addEventListener("click", () => { dismiss(op.run_id); box.remove(); onClose(); });
    box.appendChild(close);
    return box;
  }

  // Sin duplicar al volver: mientras haya una operación PROPIA en curso del mismo tipo, el botón de la herramienta queda deshabilitado
  // (solo UX; los candados del servidor siguen siendo la garantía: OPERATION_ALREADY_RUNNING / CATALOG_BUSY).
  function lockForms(ops) {
    const running = (kind) => ops.find((op) => op.state === "RUNNING" && op.kind === kind && !attached.has(op.run_id));
    [["publication", "[data-dsp-submit]", "Hay una publicación tuya en curso: esperá a que termine."],
     ["assessment", "[data-dsa-submit]", "Hay un assessment tuyo en curso: esperá a que termine."]].forEach(([kind, sel, hint]) => {
      document.querySelectorAll(sel).forEach((btn) => {
        const op = running(kind);
        let note = btn.parentElement && btn.parentElement.querySelector("[data-op-lock-note]");
        if (op) {
          if (!btn.disabled) { btn.disabled = true; btn.dataset.opLocked = "1"; }
          if (!note) {
            note = node("p", "admin-help");
            note.dataset.opLockNote = "1";
            btn.parentElement.appendChild(note);
          }
          note.replaceChildren(document.createTextNode(hint + " "), link(op.log_url, "Ver ejecución"));
        } else {
          if (btn.dataset.opLocked) { btn.disabled = false; delete btn.dataset.opLocked; }
          if (note) note.remove();
        }
      });
    });
  }

  function initNotice() {
    const host = document.querySelector("[data-copilot-operations]");
    if (!host) return;
    let timer = null;
    async function refresh() {
      timer = null;
      let ops;
      try {
        const r = await fetch("/copilot/operations", { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } });
        if (!r.ok) return;                                      // sin sesión o error: no hay aviso (nunca bloquea la página)
        ops = (await r.json()).operations || [];
      } catch (e) {
        return;
      }
      lockForms(ops);
      const dismissed = readDismissed();
      const shown = ops.filter((op) => !dismissed.has(op.run_id) && !attached.has(op.run_id)).slice(0, MAX_SHOWN);
      const signature = shown.map((op) => op.run_id + ":" + op.state).join("|");
      if (signature !== host.dataset.signature) {               // solo se redibuja si algo cambió (no pisa un clic ni un foco en curso)
        host.dataset.signature = signature;
        host.replaceChildren(...shown.map((op) => banner(op, () => { host.hidden = !host.children.length; host.dataset.signature = ""; })));
      }
      host.hidden = !shown.length;
      if (ops.some((op) => op.state === "RUNNING")) timer = setTimeout(refresh, POLL_MS);
    }
    window.dwCopilotOps.refresh = () => { if (timer) clearTimeout(timer); refresh(); };
    refresh();
  }

  // «Ver ejecución» de una operación en curso: sigue el avance (mismos eventos saneados) hasta el `done` real.
  function initLivePage() {
    const page = document.querySelector("[data-op-live-url]");
    if (!page) return;
    const url = page.dataset.opLiveUrl;
    const log = document.querySelector("[data-op-log]");
    let since = Number(page.dataset.opNext || 0);
    async function poll() {
      let data;
      try {
        const r = await fetch(url + "?since=" + since, { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } });
        if (r.status === 404) return;                           // venció: queda lo último mostrado
        if (!r.ok) throw new Error("HTTP " + r.status);
        data = await r.json();
      } catch (e) {
        setTimeout(poll, LIVE_POLL_MS);
        return;
      }
      since = data.next;
      let done = null;
      (data.events || []).forEach((ev) => {
        if (ev.t === "line" && log) { log.textContent += ev.v + "\n"; log.scrollTop = log.scrollHeight; }
        if (ev.t === "done") done = ev;
      });
      if (!done) { setTimeout(poll, LIVE_POLL_MS); return; }
      page.querySelector("[data-op-running]").hidden = true;
      if (done.state === "SUCCESS") {
        const ok = page.querySelector("[data-op-success]");
        ok.hidden = false;
        if (done.result_url && !ok.querySelector("a")) {
          ok.appendChild(document.createTextNode(" "));
          const a = link(done.result_url, page.dataset.opKind === "publication" ? "Abrir el Catálogo" : "Abrir assessment");
          a.className = "";
          ok.appendChild(a);
        }
      } else {
        const failed = page.querySelector("[data-op-failed]");
        failed.hidden = false;
        page.querySelector("[data-op-title]").textContent = done.title || "La ejecución no se completó";
        page.querySelector("[data-op-message]").textContent = done.message || "";
      }
      if (window.dwCopilotOps.refresh) window.dwCopilotOps.refresh();
    }
    setTimeout(poll, LIVE_POLL_MS);
  }

  function init() { initNotice(); initLivePage(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();

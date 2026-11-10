// "Publicar modelo" — publicación de un modelo de SAP Datasphere al Catálogo, con avance LOCAL en tiempo real y apertura
// automática de la pestaña Catálogo al terminar. HERMANO de datasphere_assessment.js (misma mecánica NDJSON) pero con su PROPIO
// panel/estado y su PROPIA ruta: no comparte acción de negocio con el Assessment.
//
// Contrato (ver webapp/routers/datasphere_publication.py): POST (cuerpo urlencoded, jamás query string) → respuesta NDJSON:
//   {"t":"start"} · {"t":"line","v":"<línea del extractor>"}* · {"t":"phase","phase":"<id>","v":"<hito>"}* ·
//   {"t":"done","state","error_code","message","title","errors","result_url"}  (+ publicación segura: "status","durability","stages","change" CHANGED|NO_CHANGE,"assessment" {regenerated,rebased,kept,uncertain,removed: cantidad},
//   "replacement" {safe,token,reasons,displaced{space,...},exclusive[],shared[],target_space,root_object} ante un conflicto de identidad)
// Reemplazo GOBERNADO: ante POOL_IDENTITY_CONFLICT con alcance, el diálogo muestra el impacto real; «Reemplazar» existe solo si el backend lo demostró seguro y pide la
// confirmación explícita. La huella del plan (`replace_token`) viaja SOLO en la próxima publicación (un solo uso) y el servidor la revalida bajo el candado.
// Todo texto externo entra con textContent (nunca como HTML). El Client Secret se lee del formulario, se limpia del campo ANTES de
// enviar y no se guarda en ningún lado. Éxito = el backend entrega `result_url` (pestaña Catálogo) y el navegador la abre.
(function () {
  "use strict";

  // Fases semánticas del backend en las que hay trabajo en curso ("extracting" es la inicial, antes de cualquier evento).
  const WORKING = new Set(["extracting", "publishing", "registering"]);

  function el(root, sel) { return root.querySelector(sel); }

  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }

  // ── reemplazo gobernado ──
  let replaceToken = "";                                       // huella del plan confirmado (no es un secreto); se consume en la publicación que lanza el diálogo
  let replaceLabel = "";                                       // «PRODUCCION → CALIDAD» para el aviso compacto durante esa publicación
  const IDENTITY_FIELDS = new Set(["host", "token_url", "space", "datasphere_root_object", "client"]);
  const REASONS = {
    SHARED_DEPENDENCY: "Hay dependencias compartidas con otros modelos.",
    CLAIMED_BY_OTHER_MODEL: "Algún objeto en conflicto lo publicó otro modelo.",
    MULTIPLE_PREVIOUS_PUBLICATIONS: "El modelo tiene publicaciones desde más de un Space: no hay una única publicación que reemplazar.",
    UNCLAIMED_CONFLICT: "Algún objeto en conflicto no pertenece a ninguna publicación registrada.",
    UNUSABLE_MANIFESTS: "Hay registros de linaje ilegibles: no se puede demostrar qué usa cada modelo.",
    CATALOG_NOT_VERIFIABLE: "No se pudo leer el Catálogo del cliente para verificar qué modelos usan cada objeto.",
  };

  function names(list, total) {
    const all = (list || []).filter(Boolean);
    const count = Math.max(total || 0, all.length);                // dos fichas pueden compartir nombre de dominio: «y N más» cuenta fichas, no nombres
    return count > 3 ? all.slice(0, 3).join(", ") + " y " + (count - 3) + " más" : all.join(", ");
  }

  function setReplacePending(form, pending, text) {
    const hint = el(form, "[data-dsp-replace-pending]");
    hint.textContent = pending ? text : "";
    setHidden(hint, !pending);
  }

  function clearReplacement(form) {
    replaceToken = "";
    replaceLabel = "";
    setReplacePending(form, false);
  }

  // `sent`: el MISMO cuerpo del POST que acaba de fallar por el conflicto (credenciales originales incluidas, solo en memoria). Confirmar lo reenvía con la
  // huella del plan; cancelar lo descarta. No hay una segunda captura del Client Secret ni copia del valor: nunca vuelve al DOM, al token ni a mensajes.
  function showReplacement(form, r, sent) {
    const dlg = document.getElementById("dsp-replace-dialog");
    if (!dlg || !r || typeof dlg.showModal !== "function") return;
    const from = (r.displaced && r.displaced.space) || "otro Space";
    el(dlg, "[data-dsp-replace-context]").textContent = (r.root_object || "") + " · " + from + " → " + (r.target_space || "");
    el(dlg, "[data-dsp-replace-summary]").textContent = "Está publicado en el Catálogo desde " + from + ". Estos objetos difieren entre ambos Spaces:";
    const rows = el(dlg, "[data-dsp-replace-rows]");
    rows.replaceChildren();
    (r.exclusive || []).concat(r.shared || []).forEach((c) => {
      const tr = document.createElement("tr");
      const models = c.used_by_count || (c.used_by_models || []).length;
      const parts = [];
      if (models) parts.push(models + (models === 1 ? " modelo: " : " modelos: ") + names(c.used_by_models, models));
      if ((c.legacy_packages || []).length) parts.push(c.legacy_packages.length + (c.legacy_packages.length === 1 ? " paquete legacy" : " paquetes legacy"));
      if (!models && (c.other_manifests || []).length) parts.push("publicación de " + names(c.other_manifests));
      [c.object_type + "/" + c.technical_name, c.exclusive ? "Exclusivo del modelo" : "Compartido", c.exclusive ? "—" : parts.join(" · ")].forEach((text) => {
        const td = document.createElement("td");
        td.className = "left";
        td.textContent = text;
        td.title = text;                                       // valor completo siempre accesible (la celda además puede ajustar en varias líneas)
        tr.appendChild(td);
      });
      rows.appendChild(tr);
    });
    const confirm = el(dlg, "[data-dsp-replace-confirm]");
    const verdict = el(dlg, "[data-dsp-replace-verdict]");
    let pending = sent;                                        // se consume al confirmar; al cancelar/cerrar se descarta
    dlg.onclose = () => {
      if (pending) pending.delete("datasphere_client_secret");  // Cancelar: no se publica ni se reemplaza nada y el secreto deja de estar en memoria
      pending = null;
      confirm.onclick = null;
    };
    if (r.safe && r.token) {
      const shared = (r.shared || []).length;
      // Decisión del owner: el Space gobernado no es inmutable. Este diálogo es el PASO FINAL: confirma el reemplazo y lanza la publicación.
      verdict.textContent = "Se reemplaza el origen gobernado " + from + " por " + r.target_space + "." +
        (shared ? " " + shared + (shared === 1 ? " objeto compartido pasa" : " objetos compartidos pasan") + " a la versión de " + r.target_space +
          ": pueden actualizarse fichas de flujo de otros modelos del cliente." : "") +
        " La publicación anterior queda registrada como reemplazada; si algo cambió, no se modifica nada.";
      setHidden(confirm, false);
      confirm.onclick = () => {
        const resend = pending;
        pending = null;                                        // un solo uso: el cierre posterior ya no lo toca
        replaceToken = r.token;
        replaceLabel = from + " → " + r.target_space;
        dlg.close();
        run(form, resend);                                     // misma operación, mismas credenciales + huella del plan (el servidor la revalida bajo el candado)
      };
    } else {
      verdict.textContent = (r.reasons || []).map((code) => REASONS[code] || code).join(" ") + " No se puede reemplazar: no se modificó nada.";
      setHidden(confirm, true);
      confirm.onclick = null;
    }
    dlg.showModal();
  }

  async function run(form, resend) {
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

    // El cuerpo se arma ahora y el secreto se borra del campo YA: no queda en el DOM durante la corrida ni para reintentar. Un reemplazo confirmado
    // reenvía el cuerpo de la publicación que lo originó (`resend`), con sus mismas credenciales: el diálogo es modal, nada del formulario cambió.
    const body = resend || new URLSearchParams(new FormData(form));
    form.querySelectorAll('input[type="password"]').forEach((i) => { i.value = ""; });
    body.delete("replace_token");
    const authorized = replaceToken ? replaceLabel : "";
    if (replaceToken) body.set("replace_token", replaceToken);  // reemplazo confirmado: un solo uso
    clearReplacement(form);
    if (authorized) setReplacePending(form, true, "Reemplazo autorizado · " + authorized + " · la publicación está en curso y todavía no terminó.");

    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    setHidden(progress, false);
    status.textContent = status.dataset.runningText;
    setHidden(spinner, false);                                 // extracción en curso: misma fila de procesamiento, spinner visible

    let done = null;
    let opRun = null;                                          // run_id del registro del servidor (aviso global / «Ver ejecución»)
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
          if (event.t === "start") {
            opRun = event.run_id || null;
            if (window.dwCopilotOps) window.dwCopilotOps.attach(opRun);   // el aviso global no la duplica mientras este panel la sigue
          } else if (event.t === "line") {
            log.textContent += event.v + "\n";
            log.scrollTop = log.scrollHeight;
          } else if (event.t === "phase") {
            status.textContent = event.v;                      // hito veraz emitido por el backend (sin porcentajes)
            setHidden(spinner, !WORKING.has(event.phase));   // presentación: el spinner solo gira en fases con trabajo en curso
          } else if (event.t === "done") {
            done = event;
            if (window.dwCopilotOps) window.dwCopilotOps.seen(opRun);     // la vio terminar acá: el aviso global no la repite
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
    setReplacePending(form, false);                            // el resultado (éxito o error) lo informa el panel; el aviso de la corrida se retira
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
      if (done.replacement && (done.error_code === "POOL_IDENTITY_CONFLICT" || done.error_code === "REPLACEMENT_PLAN_CHANGED")) {
        showReplacement(form, done.replacement, body);        // impacto real del conflicto (y, si es seguro, el reemplazo explícito con la misma operación)
      }
    }
  }

  // Cambiar la identidad de lo que se publica invalida un reemplazo confirmado (era para OTRO plan).
  document.addEventListener("input", (event) => {
    const form = event.target && event.target.closest && event.target.closest("form[data-dsp-form]");
    if (form && replaceToken && IDENTITY_FIELDS.has(event.target.name)) clearReplacement(form);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-dsp-form")) return;
    event.preventDefault();                                   // jamás navegación de formulario clásica
    const submit = form.querySelector("[data-dsp-submit]");
    if (submit && submit.disabled) return;                    // sin doble ejecución por clicks repetidos
    run(form);
  });
})();

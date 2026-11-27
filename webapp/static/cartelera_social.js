/* cartelera_social.js — MOTOR DE INTERACCIÓN del portal de Cartelera Virtual (consumo). Compartido por Comunidad (publicaciones),
 * Novedades (contenidos de una edición) y saludos de cumpleaños: un solo componente `[data-social="<target>"]` y una sola API
 * (`/cartelera/social/…`). Reproduce las interacciones de la app original (reacciones, comentarios y respuestas, @menciones, encuestas,
 * reportes, notificaciones, publicador con encuesta y opciones de Capital Humano) con su mismo marcado (.reacts, .comments, .modal, .mmenu,
 * .toast). TODA regla (visibilidad, propiedad, aprobación, moderación) la resuelve el servidor: acá no se decide nada que importe. */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const layer = () => $("#layer");

  async function api(url, body) {
    const r = await fetch(url, body === undefined ? { credentials: "same-origin" } : { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "No se pudo completar la acción.");
    return j;
  }
  function toast(msg) {
    const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
    setTimeout(() => t.remove(), 3200);
  }
  function modal(html) {
    layer().innerHTML = `<div class="modal-bg" data-sx="bgclose"><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
    const f = $("#layer .modal input, #layer .modal textarea, #layer .modal select"); if (f) f.focus();
    return $("#layer .modal");
  }
  function closeModal() { layer().innerHTML = ""; closeMenu(); }
  function confirmBox(msg, label) {
    return new Promise(resolve => {
      const m = modal(`<h2>${esc(msg)}</h2><div class="row" style="justify-content:flex-end"><button type="button" class="btn" data-cf="no">Cancelar</button><button type="button" class="btn primary" data-cf="yes">${esc(label || "Confirmar")}</button></div>`);
      m.addEventListener("click", e => { const b = e.target.closest("[data-cf]"); if (b) { closeModal(); resolve(b.dataset.cf === "yes"); } });
    });
  }

  // ── menciones: «@nombre» → directorio interno de personas del Hub (Entra ID); se guardan como @{u:correo} ──
  let mm = null;
  function closeMenu() { if (mm) { mm.el.remove(); mm = null; } }
  async function mentionInput(ta) {
    const pos = ta.selectionStart, m = ta.value.slice(0, pos).match(/@([\wÀ-ÿ.\- ]{0,30})$/);
    if (!m || /\s{2}$/.test(m[1])) { closeMenu(); return; }
    const { people } = await api("/cartelera/social/personas?q=" + encodeURIComponent(m[1])).catch(() => ({ people: [] }));
    if (document.activeElement !== ta) return;
    closeMenu();
    if (!people.length) return;
    const el = document.createElement("div"); el.className = "mmenu"; el.setAttribute("role", "listbox");
    people.forEach((h, i) => {
      const b = document.createElement("button"); b.type = "button"; b.setAttribute("role", "option"); b.setAttribute("aria-selected", i === 0 ? "true" : "false");
      b.innerHTML = `<span class="avatar sm" aria-hidden="true">${esc(h.name.split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase())}</span><span>${esc(h.name)}</span>`;
      b.addEventListener("mousedown", e => { e.preventDefault(); pickMention(ta, h, m[0].length); });
      el.appendChild(b);
    });
    const r = ta.getBoundingClientRect();
    el.style.left = Math.min(r.left + window.scrollX, window.innerWidth - 240) + "px"; el.style.top = (r.bottom + window.scrollY + 4) + "px";
    document.body.appendChild(el);
    mm = { el, ta, hits: people, idx: 0, len: m[0].length };
  }
  function pickMention(ta, h, len) {
    const pos = ta.selectionStart, ins = "@" + h.name + " ";
    ta.value = ta.value.slice(0, pos - len) + ins + ta.value.slice(pos);
    const np = pos - len + ins.length; ta.setSelectionRange(np, np);
    (ta.__picks = ta.__picks || {})[h.name] = h.id;
    closeMenu(); ta.focus();
  }
  function toStored(ta) {
    let text = ta.value.trim();
    Object.entries(ta.__picks || {}).sort((a, b) => b[0].length - a[0].length).forEach(([name, id]) => { text = text.split("@" + name).join("@{u:" + id + "}"); });
    return text;
  }
  document.addEventListener("input", e => { if (e.target.matches && e.target.matches("textarea[data-mention]")) mentionInput(e.target); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { if (mm) closeMenu(); else if (layer() && layer().innerHTML) closeModal(); }
    if (!mm || e.target !== mm.ta) return;
    const btns = [...mm.el.children];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); mm.idx = (mm.idx + (e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length; btns.forEach((b, i) => b.setAttribute("aria-selected", i === mm.idx ? "true" : "false")); }
    if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickMention(mm.ta, mm.hits[mm.idx], mm.len); }
  });

  // ── bloque de interacción: se re-dibuja desde el servidor tras cada acción ──
  async function refresh(block, open) {
    const target = block.dataset.social, mini = block.dataset.mini ? "1" : "";
    const r = await fetch(`/cartelera/social/bloque?target=${encodeURIComponent(target)}&mini=${mini}`, { credentials: "same-origin" });
    if (!r.ok) return;
    const tmp = document.createElement("div"); tmp.innerHTML = await r.text();
    const fresh = tmp.firstElementChild; if (!fresh) return;
    if (!open) $("[data-sx-comments]", fresh).hidden = true;
    block.replaceWith(fresh);
    return fresh;
  }
  const blockOf = el => el.closest("[data-social]");
  const commentsOpen = block => !$("[data-sx-comments]", block).hidden;

  // ── publicador (alta y edición) ──
  let composer = null;
  function pollBox(poll) {
    const box = $("#cp-pollbox"), btn = $('[data-sx="togglepoll"] span');
    if (!poll) { box.innerHTML = ""; if (btn) btn.textContent = "Agregar encuesta"; return; }
    if (btn) btn.textContent = "Quitar encuesta";
    box.innerHTML = `<div class="card pad stack" style="gap:10px;background:var(--surface-2)"><span class="eyebrow">Encuesta</span>
      ${poll.options.map((o, i) => `<div class="row"><input type="text" class="cp-opt" value="${esc(o)}" placeholder="Opción ${i + 1}" style="flex:1" aria-label="Opción ${i + 1}">${poll.options.length > 2 ? `<button type="button" class="icon-btn" data-sx="rmopt" data-i="${i}" aria-label="Quitar opción">✕</button>` : ""}</div>`).join("")}
      ${poll.options.length < 8 ? `<button type="button" class="btn sm" data-sx="addopt" style="justify-self:start">+ Agregar opción</button>` : ""}
      <div class="grid2"><label class="f">Cierra el<input type="date" id="cp-pclose" value="${esc(poll.closes_at || "")}"></label>
      <label class="f">Resultados<select id="cp-presults">${[["after_vote", "Después de votar"], ["always", "Siempre"], ["after_close", "Al cierre"]].map(([v, l]) => `<option value="${v}" ${poll.results === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div></div>`;
  }
  function syncPoll() {
    if (!composer.poll) return;
    composer.poll.options = $$(".cp-opt").map(i => i.value);
    const c = $("#cp-pclose"), r = $("#cp-presults");
    if (c) composer.poll.closes_at = c.value; if (r) composer.poll.results = r.value;
  }
  function openComposer(post) {
    const tpl = $("#ct-composer-tpl"); if (!tpl) { location.href = "/cartelera/comunidad?publicar=1"; return; }
    const m = modal(tpl.innerHTML);
    composer = { post, poll: post && post.poll ? JSON.parse(JSON.stringify(post.poll)) : null, image: (post && post.image) || "", uploading: false };
    const body = $("#cp-body", m);
    if (post) {
      $("[data-cp-title]", m).textContent = "Editar publicación"; $('[data-sx="publish"]', m).textContent = "Guardar cambios";
      $("#cp-cat", m).value = post.category; $("#cp-title", m).value = post.title; body.value = post.body; body.__picks = post.mentions || {};
      $("#cp-link", m).value = post.link || "";
      if ($("#cp-official", m)) { $("#cp-official", m).checked = post.official; $("#cp-when", m).value = post.publish_at || ""; }
      if ($("#cp-consentchk", m)) $("#cp-consentchk", m).checked = !!post.consent;
    }
    const consent = () => { $("#cp-consent", m).style.display = $("#cp-cat", m).value === "personales" ? "" : "none"; };
    $("#cp-cat", m).addEventListener("change", consent); consent();
    pollBox(composer.poll);
    imageBox(composer.image ? "/cartelera/media/" + composer.image + "/display" : "");
  }
  // Imagen o flyer: el SERVIDOR valida y procesa (Pillow); acá solo se previsualiza. Contenido nativo del Hub: se guarda en el almacenamiento
  // privado del Hub, sin SharePoint ni permisos especiales del colaborador. La publicación solo guarda el id que devolvió el servidor.
  function imageBox(src) {
    const m = $("#layer .modal"); if (!m) return;
    const fig = $("[data-cp-img]", m), add = $("[data-cp-imgadd]", m);
    if (!fig) return;
    $("img", fig).src = src || ""; fig.hidden = !src; add.hidden = !!src;
  }
  function pickImage() {
    const m = $("#layer .modal"), err = $("[data-cp-error]", m), pub = $('[data-sx="publish"]', m);
    const input = document.createElement("input"); input.type = "file"; input.accept = "image/jpeg,image/png,image/webp";
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0]; if (!file) return;
      const prev = composer.image, local = URL.createObjectURL(file);
      imageBox(local); $("[data-cp-img]", m).classList.add("is-uploading"); composer.uploading = true; pub.disabled = true; err.textContent = "Subiendo imagen…";
      try {
        const fd = new FormData(); fd.append("file", file);
        const r = await fetch("/cartelera/social/media", { method: "POST", body: fd, credentials: "same-origin" });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || "No se pudo subir la imagen.");
        composer.image = j.id; imageBox(j.display); err.textContent = "";
      } catch (e) {
        composer.image = prev; imageBox(prev ? "/cartelera/media/" + prev + "/display" : ""); err.textContent = e.message;   // nada confirmado
      } finally {
        URL.revokeObjectURL(local); composer.uploading = false; pub.disabled = false;
        const fig = $("[data-cp-img]", m); if (fig) fig.classList.remove("is-uploading");
      }
    });
    input.click();
  }
  async function publish(btn) {
    syncPoll();
    const m = $("#layer .modal"), err = $("[data-cp-error]", m);
    const data = { category: $("#cp-cat", m).value, title: $("#cp-title", m).value.trim(), body: toStored($("#cp-body", m)), link: $("#cp-link", m).value.trim(),
                   poll: composer.poll, consent: $("#cp-consentchk", m) && $("#cp-consentchk", m).checked, image: composer.image || "" };
    if (composer.uploading) { err.textContent = "Esperá a que termine de subirse la imagen."; return; }
    if ($("#cp-official", m)) Object.assign(data, { official: $("#cp-official", m).checked, publish_at: $("#cp-when", m).value });
    btn.disabled = true; err.textContent = "";
    try {
      const j = await api(composer.post ? `/cartelera/comunidad/publicaciones/${composer.post.id}` : "/cartelera/comunidad/publicaciones", data);
      sessionStorage.setItem("ct-toast", j.post.status === "pending" ? "Enviado. La moderación de Comunidad lo revisa antes de publicarlo." : (composer.post ? "Cambios guardados." : "Publicado en Comunidad."));
      if (location.pathname + location.search === "/cartelera/comunidad") { location.hash = "post-" + j.post.id; location.reload(); }
      else location.assign("/cartelera/comunidad#post-" + j.post.id);
    } catch (e) { err.textContent = e.message; btn.disabled = false; }
  }

  // ── notificaciones internas (no correo) ──
  async function openNotif() {
    const j = await api("/cartelera/social/notificaciones").catch(() => ({ items: [], prefs: {} }));
    const pr = j.prefs || {};
    const m = modal(`<div class="modal-h"><h2>Notificaciones</h2><button type="button" class="icon-btn" data-sx="close" aria-label="Cerrar">✕</button></div>
      <div class="stack" style="gap:0">${j.items.length ? j.items.map(x => `<a href="${esc(x.url)}" class="mini-post" style="text-decoration:none;color:var(--text);${x.new ? "font-weight:700" : ""}"><span>${esc(x.text)}</span><span class="muted" style="font-size:12px;font-weight:400">${esc(x.when)}</span></a>`).join("") : `<div class="empty">No tenés notificaciones.</div>`}</div>
      <details><summary style="cursor:pointer;font-weight:600">Configurar avisos</summary><div class="stack" style="gap:8px;margin-top:10px">
        <label class="check"><input type="checkbox" id="np-mentions" ${pr.mentions ? "checked" : ""}> Cuando me mencionan</label>
        <label class="check"><input type="checkbox" id="np-replies" ${pr.replies ? "checked" : ""}> Respuestas y comentarios en mis publicaciones</label>
        <label class="check"><input type="checkbox" id="np-official" ${pr.official ? "checked" : ""}> Comunicaciones fijadas por Capital Humano</label>
        <button type="button" class="btn sm" data-sx="savenotif" style="justify-self:start">Guardar preferencias</button></div></details>
      <p class="muted" style="margin:0;font-size:12px">Avisos dentro de la Cartelera: no se envían por correo.</p>`);
    api("/cartelera/social/notificaciones", { seen: true }).then(() => { const d = $('[data-sx="notif"] .badge-dot'); if (d) d.remove(); }).catch(() => {});
    return m;
  }

  // ── reacciones: selector flotante (mouse, toque prolongado y teclado) + detalle ──
  const RX = { like: "Me gusta", celebrate: "Celebrar", support: "Apoyo", thanks: "Gracias", idea: "Idea", funny: "Divertido" };
  const rxIcon = (block, r) => { const svg = block && $(`.ct-rx-opt[data-r="${r}"] svg`, block); return svg ? svg.outerHTML : ""; };
  const initials = n => esc(String(n || "?").split(/\s+/).slice(0, 2).map(w => w[0] || "").join("").toUpperCase());
  function openReactions(block, who) {
    const rows = [].concat(...Object.entries(who).map(([r, names]) => names.map(n => ({ r, n })))).sort((a, b) => a.n.localeCompare(b.n, "es"));
    const types = Object.keys(RX).filter(r => (who[r] || []).length);
    modal(`<div class="modal-h"><h2>Reacciones · ${rows.length}</h2><button type="button" class="icon-btn" data-sx="close" aria-label="Cerrar">✕</button></div>
      <div class="ct-rx-tabs" role="tablist"><button type="button" class="chip" role="tab" data-sx="rxtab" data-r="all" aria-pressed="true">Todas ${rows.length}</button>${types.map(r => `<button type="button" class="chip" role="tab" data-sx="rxtab" data-r="${r}" aria-pressed="false" aria-label="${RX[r]}: ${who[r].length}" title="${RX[r]}">${rxIcon(block, r)}${who[r].length}</button>`).join("")}</div>
      <div class="ct-rx-list">${rows.map(x => `<div class="ct-rx-row" data-rx-row="${x.r}"><span class="avatar" aria-hidden="true">${initials(x.n)}<span class="ct-rx-ic ct-rx-ic--${x.r}">${rxIcon(block, x.r)}</span></span><span><b>${esc(x.n)}</b><small>${RX[x.r]}</small></span></div>`).join("") || `<p class="muted">Sin reacciones.</p>`}</div>`);
  }
  function filterReactions(r) {
    $$("#layer [data-sx='rxtab']").forEach(b => b.setAttribute("aria-pressed", b.dataset.r === r ? "true" : "false"));
    $$("#layer [data-rx-row]").forEach(row => { row.style.display = r === "all" || row.dataset.rxRow === r ? "" : "none"; });
  }
  let rxTimer = 0, rxLong = false;
  const rxOpen = (wrap, on) => { wrap.classList.toggle("is-open", on); const m = $(".ct-rx-main", wrap); if (m) m.setAttribute("aria-expanded", on ? "true" : "false"); };
  document.addEventListener("mouseover", e => {
    const wrap = e.target.closest && e.target.closest("[data-rx]"); if (!wrap || wrap.contains(e.relatedTarget)) return;
    clearTimeout(rxTimer); rxTimer = setTimeout(() => rxOpen(wrap, true), 380);
  });
  document.addEventListener("mouseout", e => {
    const wrap = e.target.closest && e.target.closest("[data-rx]"); if (!wrap || wrap.contains(e.relatedTarget)) return;
    clearTimeout(rxTimer); rxTimer = setTimeout(() => rxOpen(wrap, false), 260);
  });
  document.addEventListener("pointerdown", e => {                                   // táctil: mantener presionado abre el selector
    const main = e.target.closest && e.target.closest(".ct-rx-main"); if (!main || e.pointerType === "mouse") return;
    rxLong = false; clearTimeout(rxTimer);
    rxTimer = setTimeout(() => { rxLong = true; rxOpen(main.closest("[data-rx]"), true); }, 450);
  });
  document.addEventListener("pointerup", e => { if (e.target.closest && e.target.closest(".ct-rx-main") && e.pointerType !== "mouse") clearTimeout(rxTimer); });
  document.addEventListener("contextmenu", e => { if (e.target.closest && e.target.closest(".ct-rx-main")) e.preventDefault(); });
  document.addEventListener("click", e => {                                          // tocar afuera cierra
    $$("[data-rx].is-open").forEach(w => { if (!w.contains(e.target)) rxOpen(w, false); });
  }, true);
  document.addEventListener("keydown", e => {                                        // teclado: ↑/↓ abre; ←/→ recorre; Esc cierra
    const main = e.target.closest && e.target.closest(".ct-rx-main"), opt = e.target.closest && e.target.closest(".ct-rx-opt");
    if (main && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault(); const wrap = main.closest("[data-rx]"); rxOpen(wrap, true);
      ($(".ct-rx-opt.is-mine", wrap) || $(".ct-rx-opt", wrap)).focus();
    } else if (opt) {
      const opts = $$(".ct-rx-opt", opt.parentElement), i = opts.indexOf(opt);
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); opts[(i + (e.key === "ArrowRight" ? 1 : -1) + opts.length) % opts.length].focus(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); const wrap = opt.closest("[data-rx]"); rxOpen(wrap, false); $(".ct-rx-main", wrap).focus(); }
    }
  }, true);
  document.addEventListener("focusout", e => {
    const wrap = e.target.closest && e.target.closest("[data-rx]");
    if (wrap && !wrap.contains(e.relatedTarget)) setTimeout(() => { if (!wrap.contains(document.activeElement) && !wrap.matches(":hover")) rxOpen(wrap, false); }, 0);
  });

  // ── acciones ──
  document.addEventListener("click", async e => {
    const el = e.target.closest && e.target.closest("[data-sx]");
    if (!el) return;
    const a = el.dataset.sx, block = blockOf(el);
    if (a === "bgclose") { if (e.target === el) closeModal(); return; }
    e.preventDefault();
    try {
      if (a === "close") closeModal();
      else if (a === "react") {
        if (el.classList.contains("ct-rx-main") && rxLong) { rxLong = false; return; }
        await api("/cartelera/social/reaccion", { target: block.dataset.social, r: el.dataset.r });
        const fresh = await refresh(block, commentsOpen(block));
        const main = fresh && $(".ct-rx-main", fresh); if (main && el.classList.contains("ct-rx-opt")) main.focus();
      }
      else if (a === "togglecmt") {                                                 // en un saludo el campo está siempre a la vista: solo lo enfoca
        const c = $("[data-sx-comments]", block);
        if (block.dataset.ctx !== "greeting") c.hidden = !c.hidden;
        if (!c.hidden) $("[data-sx-input]", block).focus();
      }
      else if (a === "reply") {
        const c = $("[data-sx-comments]", block); c.hidden = false; block.dataset.parent = el.dataset.id;
        const r = $("[data-sx-replying]", block); r.hidden = false; $("b", r).textContent = el.dataset.name;
        $("[data-sx-input]", block).focus();
      }
      else if (a === "cancelreply") { delete block.dataset.parent; $("[data-sx-replying]", block).hidden = true; }
      else if (a === "sendcmt") {
        const ta = $("[data-sx-input]", block), body = toStored(ta); if (!body) return;
        el.disabled = true;
        await api("/cartelera/social/comentarios", { target: block.dataset.social, body, parent: block.dataset.parent || "" });
        await refresh(block, true);
      }
      else if (a === "editcmt") {
        const t = $(`#cmt-${el.dataset.id} .t`); if (!t || t.querySelector("textarea")) return;
        t.innerHTML = `<div class="cinput"><textarea data-mention aria-label="Editar comentario">${esc(t.dataset.raw)}</textarea><button type="button" class="btn primary sm" data-sx="savecmt" data-id="${el.dataset.id}">Guardar</button><button type="button" class="btn sm" data-sx="canceledit">Cancelar</button></div>`;
        $("textarea", t).focus();
      }
      else if (a === "canceledit") await refresh(block, true);
      else if (a === "savecmt") { const ta = el.parentElement.querySelector("textarea"); await api(`/cartelera/social/comentarios/${el.dataset.id}`, { body: toStored(ta) }); await refresh(block, true); }
      else if (a === "delcmt") { if (await confirmBox("¿Eliminar tu comentario?", "Eliminar")) { await api(`/cartelera/social/comentarios/${el.dataset.id}/eliminar`, {}); await refresh(block, true); } }
      else if (a === "who") openReactions(block, JSON.parse(el.dataset.who || "{}"));
      else if (a === "rxtab") filterReactions(el.dataset.r);
      else if (a === "report") {
        const kind = el.dataset.kind, id = el.dataset.id;
        modal(`<div class="modal-h"><h2>Reportar ${kind === "post" ? "publicación" : "comentario"}</h2><button type="button" class="icon-btn" data-sx="close" aria-label="Cerrar">✕</button></div>
          <p class="muted" style="margin:0">El reporte lo ve solo la moderación de Comunidad.</p>
          <label class="f">Motivo<select id="rp-reason"><option>Contenido ofensivo o inapropiado</option><option>Información confidencial</option><option>Información personal sin consentimiento</option><option>Spam o fuera de tema</option><option>Otro</option></select></label>
          <label class="f">Comentario (opcional)<textarea id="rp-note" rows="3"></textarea></label>
          <div class="row" style="justify-content:flex-end"><button type="button" class="btn" data-sx="close">Cancelar</button><button type="button" class="btn primary" data-sx="sendreport" data-kind="${kind}" data-id="${esc(id)}">Enviar reporte</button></div>`);
      }
      else if (a === "sendreport") { await api("/cartelera/social/reportes", { kind: el.dataset.kind, id: el.dataset.id, reason: $("#rp-reason").value, note: $("#rp-note").value }); closeModal(); toast("Reporte enviado a la moderación."); }
      else if (a === "vote") { await api("/cartelera/social/voto", { post: el.dataset.id, i: Number(el.dataset.i) }); keep(); location.reload(); }
      else if (a === "compose") openComposer(null);
      else if (a === "editpost") openComposer(JSON.parse(el.dataset.postJson));
      else if (a === "togglepoll") { syncPoll(); composer.poll = composer.poll ? null : { options: ["", ""], closes_at: "", results: "after_vote" }; pollBox(composer.poll); }
      else if (a === "addopt") { syncPoll(); composer.poll.options.push(""); pollBox(composer.poll); }
      else if (a === "rmopt") { syncPoll(); composer.poll.options.splice(Number(el.dataset.i), 1); pollBox(composer.poll); }
      else if (a === "publish") await publish(el);
      else if (a === "cpimg") pickImage();
      else if (a === "cpimgdel") { composer.image = ""; imageBox(""); }
      else if (a === "delpost") { if (await confirmBox("¿Eliminar esta publicación? Se eliminan también sus comentarios y reacciones. No se puede deshacer.", "Eliminar")) { await api(`/cartelera/comunidad/publicaciones/${el.dataset.id}/eliminar`, {}); sessionStorage.setItem("ct-toast", "Publicación eliminada."); location.reload(); } }
      else if (a === "notif") await openNotif();
      else if (a === "savenotif") { await api("/cartelera/social/notificaciones", { notif: { mentions: $("#np-mentions").checked, replies: $("#np-replies").checked, official: $("#np-official").checked } }); closeModal(); toast("Preferencias guardadas."); }
    } catch (err) { toast(err.message); el.disabled = false; }
  });
  const keep = () => sessionStorage.setItem("ct-scroll", String(window.scrollY));

  // ── llegada: aviso pendiente, scroll conservado, publicación enfocada, publicador abierto ──
  const t = sessionStorage.getItem("ct-toast"); if (t) { sessionStorage.removeItem("ct-toast"); toast(t); }
  // Tras recargar, la posición la decide el portal (no el navegador): la guardada o la publicación del ancla, cuando todo cargó.
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  const arrive = () => {
    const y = sessionStorage.getItem("ct-scroll"); if (y) { sessionStorage.removeItem("ct-scroll"); window.scrollTo(0, Number(y)); return; }
    const greet = location.hash === "#saludar" && $('[data-ctx="greeting"] [data-sx-input]');   // «Saludar» desde Inicio: directo al campo
    if (greet) { greet.scrollIntoView({ block: "center" }); greet.focus({ preventScroll: true }); return; }
    const el = location.hash.length > 1 && document.getElementById(location.hash.slice(1));
    if (el) { el.scrollIntoView({ block: "center" }); el.style.outline = "2px solid var(--gold)"; setTimeout(() => { el.style.outline = ""; }, 1800); }
  };
  if (document.readyState === "complete") arrive(); else window.addEventListener("load", arrive, { once: true });
  if (new URLSearchParams(location.search).get("publicar") === "1" && $("#ct-composer-tpl")) openComposer(null);
})();

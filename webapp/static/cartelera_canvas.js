/* cartelera_canvas.js — capa de EDICIÓN del lienzo de Cartelera Virtual (solo `edit_mode`, nunca en el portal de consumo).
 *
 * El lienzo es la MISMA plantilla del portal (`cartelera/portal.html`); acá se edita sobre ella, como en Presentaciones/Estrategia:
 *   - textos: hojas `contenteditable="plaintext-only"` marcadas con `data-ct-f` (texto plano; Enter solo en las de varias líneas);
 *   - estructura: herramientas `contenteditable=false` (`.ct-ed`) con los controles de la app original (`.btn sm`, `.icon-btn`, `.grip`) y su
 *     «modo ordenar» llevado al lienzo: arrastrar secciones y contenidos (también entre secciones compatibles) con `.dragging` / `.dropover`;
 *   - el MODELO que se guarda se lee del DOM en `serialize()` (nunca se guarda HTML).
 * La página del Hub que lo aloja (`cartelera_edicion.html`) maneja guardado, borradores locales, publicación, confirmaciones y el selector de
 * proyectos de Operaciones; se comunican por `window.ctCanvas` (este archivo) y `window.parent.ctHost` (la página del Hub). */
(function () {
  "use strict";
  const HTTPS = /^https:\/\/[^\s<>"]+$/;
  const FEATURED = 6;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const host = () => (window.parent && window.parent !== window && window.parent.ctHost) || null;
  const uid = () => Math.random().toString(36).slice(2, 14);
  let canvas, drag = null, listeners = [];

  // ── modelo ←→ DOM ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
  const text = el => (el ? el.innerText.replace(/ /g, " ").replace(/\n{3,}/g, "\n\n").trim() : "");
  const field = (item, f) => text($('[data-ct-f="' + f + '"]', item));
  function readItem(item, kind) {
    const link = (item.dataset.link || "").trim();
    if (kind === "projects") {
      return { id: item.dataset.id, project_id: item.dataset.projectId || "", project_name: text($('[data-ct-ro="project_name"]', item)),
               client_name: text($('[data-ct-ro="client_name"]', item)).replace(/^—$/, ""), kind: item.dataset.kind === "fin" ? "fin" : "inicio",
               service: field(item, "service"), tech: field(item, "tech"), body: field(item, "body"), team: field(item, "team"),
               thanks: field(item, "thanks"), link };
    }
    return { id: item.dataset.id, title: field(item, "title"), body: field(item, "body"), link, link_label: link ? field(item, "link_label") : "" };
  }
  function sections() { return $$("[data-ct-sec]", canvas); }
  function serialize() {
    const greet = $("[data-ct-greeting] [data-ct-item]");
    if (greet) { const it = readItem(greet, "general"); delete it.id; return it; }
    return {
      title: text($('.cart-banner [data-ct-f="title"]')), subtitle: text($('.cart-banner [data-ct-f="subtitle"]')),
      sections: sections().map(sec => ({ key: sec.dataset.ctSec, wide: sec.dataset.ctKind === "general" && sec.classList.contains("wide"),
        items: $$("[data-ct-item]", sec).map(it => readItem(it, sec.dataset.ctKind)) })),
    };
  }
  const filled = (item, kind) => kind === "projects" ? !!item.dataset.projectId : !!(field(item, "title") || field(item, "body"));

  // ── estado visual derivado (lo que el portal hará con este contenido) ────────────────────────────────────────────────────────
  function refresh() {
    let n = 0;
    const secs = sections();
    secs.forEach((sec, si) => {
      const kind = sec.dataset.ctKind, items = $$("[data-ct-item]", sec);
      items.forEach((it, ii) => {
        const ok = filled(it, kind);
        const feat = $(".ct-feat", it);
        if (feat) feat.hidden = !((kind === "general" || kind === "projects") && ok && n < FEATURED);
        if ((kind === "general" || kind === "projects") && ok) n++;
        it.classList.toggle("ct-is-empty", !ok);
        const up = $('[data-ct-act="up"]', it), down = $('[data-ct-act="down"]', it);
        if (up) up.disabled = ii === 0;
        if (down) down.disabled = ii === items.length - 1;
        refreshLink(it);
      });
      const hidden = kind !== "birthdays" && !(kind === "links" && $(".links", sec)) && !items.some(it => filled(it, kind));
      sec.classList.toggle("ct-is-hidden", hidden);
      const tag = $(".ct-hidden-tag", sec); if (tag) tag.hidden = !hidden;
      const wide = $('[data-ct-act="wide"]', sec); if (wide) wide.textContent = sec.classList.contains("wide") ? "Poner al costado" : "Ancho completo";
      const movable = secs.filter(s => s.dataset.ctKind !== "links");
      const up = $('[data-ct-act="secup"]', sec), down = $('[data-ct-act="secdown"]', sec);
      if (up) up.disabled = si === 0;
      if (down) down.disabled = si >= movable.length - 1;
    });
    // chips de navegación: mismo orden que las secciones; las ocultas, atenuadas
    const nav = $("[data-ct-nav]");
    if (nav) secs.forEach(sec => { const chip = $('[data-ct-chip="' + sec.dataset.ctSec + '"]', nav); if (chip) { nav.appendChild(chip); chip.classList.toggle("ct-is-hidden", sec.classList.contains("ct-is-hidden")); } });
    $$("[data-ct-greeting] [data-ct-item]").forEach(refreshLink);
    renderFeatured();
    const title = text($('.cart-banner [data-ct-f="title"]')), sub = text($('.cart-banner [data-ct-f="subtitle"]'));
    $$('[data-ct-echo="title"]').forEach(el => { el.textContent = title || "Novedades, bienestar, aprendizaje, cumpleaños y accesos directos."; });
    $$("[data-ct-sep]").forEach(el => { el.hidden = !sub; });
    $$("[data-ct-chips]").forEach(row => {
      const src = $('[data-ct-f="tech"]', row.parentElement);
      const techs = text(src).split(",").map(t => t.trim()).filter(Boolean);
      row.replaceChildren(...techs.map(t => { const c = document.createElement("span"); c.className = "chip"; c.style.cursor = "default"; c.textContent = t; return c; }));
      row.hidden = !techs.length;
    });
  }
  function refreshLink(it) {
    const link = (it.dataset.link || "").trim(), bad = !!link && !HTTPS.test(link);
    it.classList.toggle("ct-has-error", bad);
    const err = $(".ct-linkerr", it); if (err) err.hidden = !bad;
    const card = $("[data-ct-linkcard]", it); if (card) card.hidden = !link;
    const lbl = $('[data-ct-f="link_label"]', it); if (lbl) lbl.dataset.ph = link || "Ver más";
    const btn = $('[data-ct-act="link"]', it); if (btn) btn.classList.toggle("is-on", !!link);
  }
  function changed() { refresh(); listeners.forEach(fn => { try { fn(); } catch (e) {} }); }

  // ── Inicio se DERIVA de Novedades (una sola fuente de verdad): la portada del Inicio es un espejo editable de la de Novedades y
  //    «Destacado» se recalcula con los primeros contenidos, igual que en el portal. Nada de Inicio se serializa por separado. ──
  function syncMirror(fieldName, from) {
    const value = from.innerText;
    [...$$('.cart-banner [data-ct-f="' + fieldName + '"]'), ...$$('[data-ct-mirror="' + fieldName + '"]')].forEach(el => {
      if (el !== from && el.innerText !== value) el.textContent = value;
    });
  }
  function renderFeatured() {
    const box = $("[data-ct-featured]");
    if (!box) return;
    const rows = [];
    sections().forEach(sec => {
      const kind = sec.dataset.ctKind;
      if (kind !== "general" && kind !== "projects") return;
      $$("[data-ct-item]", sec).forEach(it => {
        if (rows.length >= FEATURED || !filled(it, kind)) return;
        const label = kind === "projects" ? text($('[data-ct-ro="project_name"]', it)) : (field(it, "title") || field(it, "body").slice(0, 80));
        const client = kind === "projects" ? text($('[data-ct-ro="client_name"]', it)).replace(/^—$/, "") : "";
        rows.push({ id: it.dataset.id, sec: text($("h2", sec)), label, client });
      });
    });
    box.replaceChildren(...(rows.length ? rows.map(r => {
      const div = document.createElement("div"); div.className = "mini-post";
      const head = document.createElement("div"); head.className = "row"; head.style.gap = "6px";
      const tag = document.createElement("span"); tag.className = "tag cat"; tag.textContent = r.sec; head.appendChild(tag);
      const a = document.createElement("a"); a.href = "#"; a.dataset.ctGoto = r.id; a.textContent = r.label; a.title = "Editar en Novedades";
      div.append(head, a);
      if (r.client) { const c = document.createElement("span"); c.className = "muted"; c.style.fontSize = "12px"; c.textContent = r.client; div.appendChild(c); }
      return div;
    }) : [Object.assign(document.createElement("div"), { className: "empty", textContent: "Todavía no hay novedades publicadas." })]));
  }
  let current = "inicio";
  function showPage(pg, target) {
    if (!$('[data-ct-page="' + pg + '"]')) return;
    current = pg;
    $$("[data-ct-page]").forEach(el => { el.hidden = el.dataset.ctPage !== pg; });
    $$("[data-ct-pages] a").forEach(a => { if (pageOf(a.getAttribute("href")) === pg) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
    const H = host(); if (H && H.pageChanged) H.pageChanged(pg);
    if (target) target.scrollIntoView({ block: "center" }); else window.scrollTo(0, 0);
  }
  function pageOf(href) {
    const path = (href || "").split("#")[0];
    if (/^\/cartelera\/novedades/.test(path)) return "novedades";
    if (/^\/cartelera\/nosotros/.test(path)) return "nosotros";
    if (/^\/cartelera\/?$/.test(path)) return "inicio";
    return null;
  }

  // ── textos: texto plano, Enter solo donde corresponde, sin navegación ───────────────────────────────────────────────────────────
  function wireText(root) {
    $$("[data-ct-f], [data-ct-mirror]", root).forEach(el => {
      if (el.dataset.ctWired) return;
      el.dataset.ctWired = "1";
      el.addEventListener("keydown", ev => {
        if (ev.key === "Enter" && !el.hasAttribute("data-multi")) { ev.preventDefault(); el.blur(); }
        if (ev.key === "Escape") el.blur();
      });
      el.addEventListener("paste", ev => {
        ev.preventDefault();
        let t = (ev.clipboardData || window.clipboardData).getData("text/plain");
        if (!el.hasAttribute("data-multi")) t = t.replace(/\s*\n\s*/g, " ");
        document.execCommand("insertText", false, t);
      });
      el.addEventListener("input", () => {
        if (!el.textContent.trim() && el.innerHTML !== "") el.innerHTML = "";
        const f = el.dataset.ctMirror || (el.closest(".cart-banner") ? el.dataset.ctF : "");
        if (f === "title" || f === "subtitle") syncMirror(f, el);
        changed();
      });
    });
    $$("[data-ct-link]", root).forEach(inp => {
      if (inp.dataset.ctWired) return;
      inp.dataset.ctWired = "1";
      inp.addEventListener("input", () => { inp.closest("[data-ct-item]").dataset.link = inp.value.trim(); changed(); });
    });
    // Texto del botón: el campo del panel «Enlace» y el texto editable sobre la tarjeta son el MISMO dato (`link_label`), sincronizados.
    // Está disponible desde que se agrega un enlace nuevo (antes solo se podía editar sobre una tarjeta ya existente).
    $$("[data-ct-link-label]", root).forEach(inp => {
      if (inp.dataset.ctWired) return;
      inp.dataset.ctWired = "1";
      const inline = () => $('[data-ct-f="link_label"]', inp.closest("[data-ct-item]"));
      inp.addEventListener("input", () => { const el = inline(); if (el) el.textContent = inp.value; changed(); });
      const el = inline();
      if (el) el.addEventListener("input", () => { inp.value = el.textContent; });
    });
  }

  // ── acciones ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  function focusEnd(el) {
    if (!el) return;
    el.focus();
    const r = document.createRange(); r.selectNodeContents(el); r.collapse(false);
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  }
  function newItem(kind) {
    const tpl = $('template[data-ct-tpl="' + (kind === "projects" ? "projects" : "general") + '"]');
    const item = tpl.content.firstElementChild.cloneNode(true);
    item.dataset.id = uid();
    wireText(item);
    return item;
  }
  function applyProject(item, p) {
    item.dataset.projectId = p.id;
    $('[data-ct-ro="project_name"]', item).textContent = p.name;
    $('[data-ct-ro="client_name"]', item).textContent = p.client || "—";
  }
  function usedProjects(except) { return $$("[data-ct-sec] [data-ct-item][data-project-id]").filter(it => it !== except && it.dataset.projectId).map(it => it.dataset.projectId); }

  async function act(btn) {
    const a = btn.dataset.ctAct, item = btn.closest("[data-ct-item]"), sec = btn.closest("[data-ct-sec]"), H = host();
    if (a === "wide") { sec.classList.toggle("wide"); changed(); return; }
    if (a === "secup" || a === "secdown") {
      const all = sections().filter(s => s.dataset.ctKind !== "links"), i = all.indexOf(sec), j = a === "secup" ? i - 1 : i + 1;
      if (j < 0 || j >= all.length) return;
      if (a === "secup") all[j].before(sec); else all[j].after(sec);
      sec.scrollIntoView({ block: "nearest" }); changed(); return;
    }
    if (a === "up" || a === "down") {
      const sib = a === "up" ? item.previousElementSibling : item.nextElementSibling;
      if (!sib || !sib.matches("[data-ct-item]")) return;
      if (a === "up") sib.before(item); else sib.after(item);
      changed(); return;
    }
    if (a === "link") {
      const panel = $(".ct-linkpanel", item); panel.hidden = !panel.hidden;
      if (!panel.hidden) $("[data-ct-link]", panel).focus();
      return;
    }
    if (a === "kind") {
      const fin = item.dataset.kind !== "fin";
      item.dataset.kind = fin ? "fin" : "inicio";
      btn.classList.toggle("done", fin); btn.classList.toggle("off", !fin);
      $("span", btn).textContent = fin ? "Proyecto finalizado" : "Nuevo proyecto";
      changed(); return;
    }
    if (a === "del") {
      const kind = sec.dataset.ctKind, name = kind === "projects" ? text($('[data-ct-ro="project_name"]', item)) : (field(item, "title") || "este contenido");
      if (filled(item, kind) && H && !(await H.confirm({ title: "Quitar contenido", message: "Se quita «" + name + "» de la edición. El cambio se confirma al guardar.", confirmLabel: "Quitar" }))) return;
      item.remove(); changed(); return;
    }
    if (a === "add") {
      const item2 = newItem("general");
      $("[data-ct-items]", sec).appendChild(item2);
      changed(); focusEnd($('[data-ct-f="title"]', item2)); item2.scrollIntoView({ block: "nearest" });
      return;
    }
    if (a === "addproj" || a === "swap") {
      if (!H) return;
      const p = await H.pickProject({ used: usedProjects(item), replacing: a === "swap" });
      if (!p) return;
      if (a === "swap") { applyProject(item, p); changed(); return; }
      const item2 = newItem("projects");
      applyProject(item2, p);
      $("[data-ct-items]", sec).appendChild(item2);
      changed(); focusEnd($('[data-ct-f="service"]', item2)); item2.scrollIntoView({ block: "nearest" });
    }
  }

  // ── arrastrar y soltar (el «modo ordenar» de la app original, sobre el lienzo) ─────────────────────────────────────────────────
  const compatible = (fromKind, toKind) => toKind !== "birthdays" && (fromKind === "projects") === (toKind === "projects");
  function clearMarks() { $$(".dropover").forEach(x => x.classList.remove("dropover")); }
  function wireDrag() {
    document.addEventListener("dragstart", ev => {
      const grip = ev.target.closest && ev.target.closest("[data-ct-grip]");
      if (!grip) return;
      const el = grip.dataset.ctGrip === "sec" ? grip.closest("[data-ct-sec]") : grip.closest("[data-ct-item]");
      drag = { t: grip.dataset.ctGrip, el, kind: el.closest("[data-ct-sec]").dataset.ctKind };
      ev.dataTransfer.effectAllowed = "move";
      try { ev.dataTransfer.setData("text/plain", "mover"); ev.dataTransfer.setDragImage(el, 20, 20); } catch (e) {}
      requestAnimationFrame(() => el.classList.add("dragging"));
    });
    document.addEventListener("dragover", ev => {
      if (!drag) return;
      const sec = ev.target.closest && ev.target.closest("[data-ct-sec]");
      if (!sec) return;
      if (drag.t === "sec" ? sec.dataset.ctKind === "links" : !compatible(drag.kind, sec.dataset.ctKind)) return;
      ev.preventDefault();
      const t = drag.t === "item" ? (ev.target.closest("[data-ct-item]") || sec) : sec;
      clearMarks(); if (t !== drag.el) t.classList.add("dropover");
    });
    document.addEventListener("drop", ev => {
      if (!drag) return;
      ev.preventDefault();
      const sec = ev.target.closest && ev.target.closest("[data-ct-sec]");
      if (sec && drag.t === "sec" && sec !== drag.el && sec.dataset.ctKind !== "links") {
        const all = sections(), from = all.indexOf(drag.el), to = all.indexOf(sec);
        if (from < to) sec.after(drag.el); else sec.before(drag.el);
      } else if (sec && drag.t === "item" && compatible(drag.kind, sec.dataset.ctKind)) {
        const target = ev.target.closest("[data-ct-item]");
        if (target && target !== drag.el) {
          const r = target.getBoundingClientRect(), after = (ev.clientY - r.top) > r.height / 2;
          if (after) target.after(drag.el); else target.before(drag.el);
        } else if (!target) $("[data-ct-items]", sec).appendChild(drag.el);
      }
      drag.el.classList.remove("dragging"); drag = null; clearMarks(); changed();
    });
    document.addEventListener("dragend", () => { if (drag) drag.el.classList.remove("dragging"); drag = null; clearMarks(); });
  }

  // ── arranque ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  function init() {
    canvas = $("[data-ct-canvas]") || $("[data-ct-greeting]");
    if (!canvas) return;
    wireText(document);
    wireDrag();
    document.addEventListener("click", ev => {
      const btn = ev.target.closest && ev.target.closest("[data-ct-act]");
      if (btn) { ev.preventDefault(); if (!btn.disabled) act(btn); return; }
      const go = ev.target.closest && ev.target.closest("[data-ct-goto]");
      if (go) {                                                                       // Destacado → el contenido, en Novedades
        ev.preventDefault();
        const it = $('[data-ct-item][data-id="' + go.dataset.ctGoto + '"]');
        showPage("novedades", it);
        if (it) { const f = $("[data-ct-f]", it); if (f) focusEnd(f); }
        return;
      }
      const a = ev.target.closest && ev.target.closest("a[href]");
      if (a) {                                                                        // el lienzo no sale del editor: recorre sus páginas
        ev.preventDefault();
        const href = a.getAttribute("href") || "", hash = href.includes("#") ? href.slice(href.indexOf("#") + 1) : "";
        const pg = href.startsWith("#") ? null : pageOf(href);
        if (pg) showPage(pg, hash ? document.getElementById(hash) : null);
        else if (hash) { const t = document.getElementById(hash); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); }
      }
    });
    $$("select").forEach(s => { s.disabled = true; });
    const vis = $("[data-ct-page]:not([hidden])"); if (vis) current = vis.dataset.ctPage;
    refresh();
    window.ctCanvas = {
      serialize, refresh,
      onChange(fn) { listeners.push(fn); },
      invalidLinks() { return $$("[data-ct-item].ct-has-error"); },
      revealError() {
        const it = $("[data-ct-item].ct-has-error");
        if (!it) return;
        $(".ct-linkpanel", it).hidden = false; it.scrollIntoView({ block: "center" }); $("[data-ct-link]", it).focus();
      },
      pruneEmpty() { sections().forEach(sec => $$("[data-ct-item]", sec).forEach(it => { if (sec.dataset.ctKind !== "projects" && !filled(it, sec.dataset.ctKind)) it.remove(); })); refresh(); },
      page: () => current, showPage,
      scrollTo(key) { const s = document.getElementById("sec-" + key); if (s) s.scrollIntoView({ block: "start" }); },
    };
    const H = host();
    if (H && H.canvasReady) H.canvasReady(window.ctCanvas);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();

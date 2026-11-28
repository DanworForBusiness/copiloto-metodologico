/* Hub · selector de reacciones transversal (`partials/dw_reaction_picker.html`): MISMO patrón de interacción que Comunidad en Cartelera
   Virtual. Un botón neutro en reposo; el selector flotante se abre al pasar el mouse (con una pequeña demora de cierre), al tocar o al
   hacer clic sobre el botón, y por teclado (Enter/Espacio/↑ abren y enfocan la primera opción; ←/→ recorren; Escape cierra y devuelve el
   foco). Click fuera o foco fuera cierran. Delegado en `document`: también gobierna los selectores que HTMX inserta después. No persiste
   nada: cada opción es un botón `submit` del formulario del consumidor. */
(function () {
  'use strict';
  if (window.__dwReactions) return;
  window.__dwReactions = true;
  var SEL = '[data-dw-rxp]';
  var timers = new WeakMap();

  function open(w, on) {
    if (!w) return;
    clearTimeout(timers.get(w));
    w.classList.toggle('is-open', on);
    var m = w.querySelector('.dw-rxp-main');
    if (m) m.setAttribute('aria-expanded', on ? 'true' : 'false');
  }
  function closeAll(except) {
    document.querySelectorAll(SEL + '.is-open').forEach(function (w) { if (w !== except) open(w, false); });
  }
  function later(w) { timers.set(w, setTimeout(function () { open(w, false); }, 220)); }

  document.addEventListener('pointerover', function (e) {
    var w = e.target.closest && e.target.closest(SEL);
    if (!w || e.pointerType !== 'mouse') return;
    closeAll(w); open(w, true);
  });
  document.addEventListener('pointerout', function (e) {
    var w = e.target.closest && e.target.closest(SEL);
    if (!w || e.pointerType !== 'mouse' || (e.relatedTarget && w.contains(e.relatedTarget))) return;
    later(w);
  });
  // «Tengo una idea»: la lamparita NO es un voto; revela el formulario de idea del propio widget (`[data-dw-feedback]`) y oculta la barra.
  function idea(scope, on) {
    var form = scope && scope.querySelector('[data-dw-idea]'), bar = scope && scope.querySelector('[data-dw-feedback-bar]');
    if (!form) return;
    form.hidden = !on;
    if (bar) bar.hidden = on;
    if (on) { var t = form.querySelector('textarea'); if (t) t.focus(); }
    else { var m = bar && bar.querySelector('.dw-rxp-main'); if (m) m.focus(); }
  }
  document.addEventListener('click', function (e) {
    var ideaOpen = e.target.closest && e.target.closest('[data-dw-idea-open]');
    if (ideaOpen) { e.preventDefault(); closeAll(null); idea(ideaOpen.closest('[data-dw-feedback]'), true); return; }
    var ideaCancel = e.target.closest && e.target.closest('[data-dw-idea-cancel]');
    if (ideaCancel) { e.preventDefault(); idea(ideaCancel.closest('[data-dw-feedback]'), false); return; }
    var main = e.target.closest && e.target.closest('.dw-rxp-main');
    var w = main && main.closest(SEL);
    if (w) { e.preventDefault(); var on = !w.classList.contains('is-open'); closeAll(w); open(w, on); return; }
    if (!(e.target.closest && e.target.closest(SEL))) closeAll(null);
  });
  document.addEventListener('keydown', function (e) {
    var main = e.target.closest && e.target.closest('.dw-rxp-main');
    var opt = e.target.closest && e.target.closest('.dw-rxp .dw-rx-opt');
    if (main && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowUp')) {
      var w = main.closest(SEL);
      if (!w) return;
      e.preventDefault(); closeAll(w); open(w, true);
      var first = w.querySelector('.dw-rx-opt'); if (first) first.focus();
      return;
    }
    if (opt) {
      var wrap = opt.closest(SEL), opts = Array.prototype.slice.call(wrap.querySelectorAll('.dw-rx-opt')), i = opts.indexOf(opt);
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); opts[(i + (e.key === 'ArrowRight' ? 1 : opts.length - 1)) % opts.length].focus(); }
      else if (e.key === 'Escape') { e.preventDefault(); open(wrap, false); wrap.querySelector('.dw-rxp-main').focus(); }
      return;
    }
    if (e.key === 'Escape') closeAll(null);
  });
  document.addEventListener('focusout', function (e) {
    var w = e.target.closest && e.target.closest(SEL);
    if (w && !(e.relatedTarget && w.contains(e.relatedTarget))) later(w);
  });

  /* ── Instancias DINÁMICAS del componente de Feedback (`partials/dw_feedback.html`) ───────────────────────────────────────────────────
     Para consumidores que renderizan en el cliente (Reporting, Comercial: JS; Staffing: Alpine/HTMX). NO hay markup del componente acá:
     se clonan los `<template data-dw-feedback-tpl>` que `base.html` renderiza UNA vez desde el macro. El consumidor solo declara un
     marcador `[data-dw-feedback-mount="<clave>"]` (Jinja: `dw_feedback_mount`; JS: `dwFeedback.html(cfg)`) con su pregunta, endpoint,
     campos del contrato e idea. Estado por CLAVE (sobrevive a cada re-render del consumidor), ids únicos por instancia y los MISMOS
     listeners delegados de arriba (picker, lamparita, Cancelar). Persistencia: `rating=up` + campos → SU endpoint (JSON `{state}`), sin
     cambiar su contrato ni sus métricas; «Tengo una idea» → `/trees/suggest` (`kind=idea`): nunca se convierte en un voto negativo. */
  var MOUNT = '[data-dw-feedback-mount]';
  var STATES = {};
  var seq = 0;

  function attr(v) { return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function clone(name) {
    var t = document.querySelector('template[data-dw-feedback-tpl="' + name + '"]');
    return t && t.content.firstElementChild ? t.content.firstElementChild.cloneNode(true) : null;
  }
  function mount(host) {
    var d = host.dataset, key = d.dwFeedbackMount, st = STATES[key];
    var node = clone(st === 'up' || st === 'idea' ? st : 'bar');
    if (!node) return;
    var id = d.dwId || (d.dwId = 'dw-feedback-' + (++seq));
    node.id = id;
    node.setAttribute('data-dw-key', key);
    if (node.querySelector('[data-dw-feedback-bar]')) {
      var bar = node.querySelector('[data-dw-feedback-bar]');
      bar.setAttribute('data-dw-action', d.action || '');
      var prompt = bar.querySelector('.section-feedback__prompt');
      if (prompt && d.prompt) prompt.textContent = d.prompt;
      var ideaForm = node.querySelector('[data-dw-idea]');
      if (ideaForm && d.ideaContext) {
        var set = function (n, v) { var i = ideaForm.querySelector('input[name="' + n + '"]'); if (i) i.value = v; };
        set('module', d.ideaModule || ''); set('context', d.ideaContext); set('sg_id', id); set('sg_class', 'section-feedback');
        var ta = ideaForm.querySelector('textarea'), lb = ideaForm.querySelector('label');
        if (ta) ta.id = id + '-idea';
        if (lb) { lb.htmlFor = id + '-idea'; if (d.ideaPrompt) lb.textContent = d.ideaPrompt; }
      } else {
        if (ideaForm) ideaForm.remove();
        var opt = node.querySelector('[data-dw-idea-open]'); if (opt) opt.remove();
      }
    }
    host.replaceChildren(node);
  }
  function mountAll(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && root.matches(MOUNT)) mount(root);
    root.querySelectorAll(MOUNT).forEach(mount);
  }
  function busy(w, on) { w.querySelectorAll('button, textarea').forEach(function (b) { b.disabled = on; }); }
  function failed(host) {
    mount(host);
    var p = host.querySelector('.section-feedback__prompt');
    if (p) p.textContent = p.textContent + ' · reintentá';
  }
  // Captura: las instancias dinámicas resuelven su envío acá (nunca llegan a htmx).
  document.addEventListener('submit', function (e) {
    var w = e.target.closest && e.target.closest('[data-dw-key]');
    var host = w && w.parentElement;
    if (!host || !host.matches(MOUNT)) return;
    e.preventDefault(); e.stopPropagation();
    var form = e.target, key = host.dataset.dwFeedbackMount, req;
    if (form.hasAttribute('data-dw-idea')) {
      req = fetch('/trees/suggest', { method: 'POST', body: new URLSearchParams(new FormData(form)), credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw r; return 'idea'; });
    } else {
      var rating = e.submitter && e.submitter.value;
      if (rating !== 'up') return;
      var body = new URLSearchParams(host.dataset.fields || '');
      body.set('rating', rating);
      req = fetch(host.dataset.action, { method: 'POST', body: body, credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) { if (!data || data.state !== 'ok') throw data; return rating; });
    }
    busy(w, true);
    req.then(function (st) { STATES[key] = st; mount(host); }, function () { failed(host); });
  }, true);

  new MutationObserver(function (records) {
    records.forEach(function (r) { r.addedNodes.forEach(function (n) { if (n.nodeType === 1) mountAll(n); }); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', function () { mountAll(document.body); });

  /* Declaración desde JS: devuelve el marcador (no el componente). cfg: {key, prompt, action, fields: {campo: valor}, idea: {module, context, prompt}} */
  window.dwFeedback = {
    html: function (cfg) {
      var idea = cfg.idea || {};
      return '<div data-dw-feedback-mount="' + attr(cfg.key) + '" data-prompt="' + attr(cfg.prompt) + '" data-action="' + attr(cfg.action) +
        '" data-fields="' + attr(new URLSearchParams(cfg.fields || {}).toString()) + '" data-idea-module="' + attr(idea.module) +
        '" data-idea-context="' + attr(idea.context) + '" data-idea-prompt="' + attr(idea.prompt) + '"></div>';
    },
    mount: mountAll,
  };
})();

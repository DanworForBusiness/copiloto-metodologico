/* Cartelera Virtual · Accesos rápidos de Inicio: búsqueda libre + cantidad dinámica + paginador, todo del lado del cliente sobre los accesos
   que ya trae el HTML (orden de Gestión → Accesos directos). Sin dependencias: el portal standalone no carga app.js ni htmx.
   - Búsqueda por nombre y grupo, sin mayúsculas ni acentos, sobre TODOS los accesos; cambiar el criterio vuelve a la página 1.
   - Capacidad = filas que entran en el alto de la lista × columnas de la grilla. La tarjeta tiene `contain:size` (`.qlinks--fit`): su alto no
     depende del contenido, por eso recalcular al redimensionar nunca realimenta el tamaño (sin saltos ni ciclos).
   - Paginador con el markup de `.activity-pager` del Hub (Primera/Anterior/ventana con «…»/Siguiente/Última) + rango «1–6 de 12»; se oculta
     si todo entra. Al cambiar la capacidad se conserva el primer acceso visible. */
(function () {
  'use strict';

  function norm(s) {
    return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
  }

  // Misma ventana que webapp/services/pagination.page_window: primera y última siempre, ±radius alrededor de la actual, null = «…».
  function pageWindow(page, total, radius) {
    if (total <= 1) return [1];
    var set = {};
    set[1] = true; set[total] = true;
    for (var p = page - radius; p <= page + radius; p++) if (p >= 1 && p <= total) set[p] = true;
    var ordered = Object.keys(set).map(Number).sort(function (a, b) { return a - b; });
    var out = [], prev = null;
    ordered.forEach(function (n) {
      if (prev !== null && n - prev > 1) out.push(null);
      out.push(n); prev = n;
    });
    return out;
  }

  function init(card) {
    var input = card.querySelector('[data-qlinks-q]');
    var clear = card.querySelector('[data-qlinks-clear]');
    var list = card.querySelector('[data-qlinks-list]');
    var empty = card.querySelector('[data-qlinks-empty]');
    var pager = card.querySelector('[data-qlinks-pager]');
    if (!list || !pager) return;

    var items = Array.prototype.map.call(list.querySelectorAll('.lnk'), function (a) {
      var span = a.querySelector('span:not(.ic)');
      var small = span && span.querySelector('small');
      var title = span ? (span.firstChild && span.firstChild.nodeType === 3 ? span.firstChild.textContent : span.textContent) : a.textContent;
      var group = small ? small.textContent : '';
      if (!a.title) a.title = group ? title.trim() + ' · ' + group.trim() : title.trim();
      return { el: a, title: norm(title), group: norm(group) };
    });

    var state = { q: '', page: 1, cap: 0, first: 0 };
    card.classList.add('qlinks--fit');

    function filtered() {
      if (!state.q) return items;
      return items.filter(function (it) { return it.title.indexOf(state.q) !== -1 || it.group.indexOf(state.q) !== -1; });
    }

    function gridMetrics() {
      var cs = getComputedStyle(list);
      var cols = (cs.gridTemplateColumns || '').split(' ').filter(Boolean).length || 1;
      var gap = parseFloat(cs.rowGap) || 0;
      return { cols: cols, gap: gap };
    }

    function itemHeight() {
      // Mide un botón visible (todos miden igual: nombre y grupo en una línea).
      var shown = null;
      for (var i = 0; i < items.length; i++) if (!items[i].el.hidden) { shown = items[i].el; break; }
      if (!shown) { items[0].el.hidden = false; shown = items[0].el; }
      return shown.getBoundingClientRect().height || 56;
    }

    function capacityFor(height, m, ih) {
      var rows = Math.max(1, Math.floor((height + m.gap) / (ih + m.gap)));
      return rows * m.cols;
    }

    function btn(cls, label, title, page, disabled, current) {
      var el = document.createElement(current ? 'span' : 'button');
      el.className = cls + (current ? ' is-active' : '');
      el.textContent = label;
      if (current) { el.setAttribute('aria-current', 'page'); return el; }
      el.type = 'button';
      if (title) el.title = title;
      if (disabled) el.disabled = true; else el.dataset.page = String(page);
      return el;
    }

    function renderPager(page, pages, from, to, total) {
      pager.textContent = '';
      pager.appendChild(btn('activity-pager__btn', '«', 'Primera página', 1, page <= 1));
      pager.appendChild(btn('activity-pager__btn', '‹', 'Anterior', page - 1, page <= 1));
      pageWindow(page, pages, 1).forEach(function (p) {
        if (p === null) {
          var e = document.createElement('span'); e.className = 'activity-pager__ellipsis'; e.textContent = '…'; pager.appendChild(e);
        } else {
          pager.appendChild(btn('activity-pager__page', String(p), 'Página ' + p, p, false, p === page));
        }
      });
      pager.appendChild(btn('activity-pager__btn', '›', 'Siguiente', page + 1, page >= pages));
      pager.appendChild(btn('activity-pager__btn', '»', 'Última página', pages, page >= pages));
      var range = document.createElement('span');
      range.className = 'qlinks-pager__range';
      range.setAttribute('aria-live', 'polite');
      range.textContent = from + '–' + to + ' de ' + total;
      pager.appendChild(range);
    }

    function render() {
      var list_ = filtered();
      var total = list_.length;
      if (clear) clear.hidden = !input.value;
      if (!total) {
        items.forEach(function (it) { it.el.hidden = true; });
        list.hidden = true; empty.hidden = false; pager.hidden = true;
        state.cap = 0;
        return;
      }
      list.hidden = false; empty.hidden = true;

      var m = gridMetrics();
      var ih = itemHeight();
      // 1) ¿Entra todo sin paginador?
      pager.hidden = true;
      var cap = capacityFor(list.clientHeight, m, ih);
      if (total > cap) {
        // 2) No entra: con paginador. Su alto se mide armado en el caso más ancho (página intermedia) por si en pantallas angostas pasa a dos líneas.
        var est = Math.max(2, Math.ceil(total / cap));
        renderPager(Math.ceil(est / 2), est, total, total, total);
        pager.hidden = false;
        cap = capacityFor(list.clientHeight, m, ih);
      }
      var pages = Math.max(1, Math.ceil(total / cap));
      if (state.cap && cap !== state.cap) state.page = Math.floor(state.first / cap) + 1;   // conservar el primer acceso visible
      state.cap = cap;
      state.page = Math.min(Math.max(1, state.page), pages);
      var start = (state.page - 1) * cap, end = Math.min(total, start + cap);
      state.first = start;

      var visible = new Set(list_.slice(start, end));
      items.forEach(function (it) { it.el.hidden = !visible.has(it); });
      if (pages > 1) { renderPager(state.page, pages, start + 1, end, total); pager.hidden = false; }
      else pager.hidden = true;
    }

    var raf = 0;
    function schedule() {
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = 0; render(); });
    }

    input.addEventListener('input', function () {
      var q = norm(input.value);
      if (q === state.q) { if (clear) clear.hidden = !input.value; return; }
      state.q = q; state.page = 1; state.cap = 0; state.first = 0;
      render();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && input.value) { e.preventDefault(); input.value = ''; input.dispatchEvent(new Event('input')); }
    });
    if (clear) clear.addEventListener('click', function () {
      input.value = ''; input.dispatchEvent(new Event('input')); input.focus();
    });
    pager.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-page]');
      if (!b) return;
      state.page = parseInt(b.dataset.page, 10) || 1;
      state.cap = 0;  // navegación explícita: no reubicar por primer visible
      render();
    });

    if (window.ResizeObserver) new ResizeObserver(schedule).observe(card);
    else window.addEventListener('resize', schedule);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
    render();
  }

  function boot() { document.querySelectorAll('[data-qlinks]').forEach(init); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();

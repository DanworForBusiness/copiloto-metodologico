(function(){
  var reduceMotion = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  function onScrollTick(){
    var vh = window.innerHeight || document.documentElement.clientHeight;

    var sections = document.querySelectorAll('section[id^="s"]');
    var current = null, bestDist = Infinity;
    sections.forEach(function(s){
      var r = s.getBoundingClientRect();
      if (r.bottom <= 0 || r.top >= vh) return;
      var dist = Math.abs((r.top + r.bottom) / 2 - vh / 2);
      if (dist < bestDist) { bestDist = dist; current = s; }
    });
    if (current) {
      document.querySelectorAll('nav.rail a[href^="#s"]').forEach(function(a){
        a.classList.toggle('active', a.getAttribute('href') === '#' + current.id);
      });
    }
  }
  var tickQueued = false;
  function scheduleTick(){
    if (tickQueued) return;
    tickQueued = true;
    requestAnimationFrame(function(){ tickQueued = false; onScrollTick(); });
  }
  window.addEventListener('scroll', scheduleTick, { passive: true });
  window.addEventListener('resize', scheduleTick);
  scheduleTick();
  setTimeout(scheduleTick, 500);
  setTimeout(scheduleTick, 1500);

  // Glow del puntero de la portada: el MISMO de la landing/login del Hub (brand-hero.css `.hero-gradient` + brand-hero.js `initMouseGradient`): un resplandor dorado enorme y muy suave que sigue
  // al puntero y queda donde lo dejó (no se apaga al salir). Solo con puntero fino (en táctil queda en su posición por defecto, como en la landing).
  var hero = document.getElementById('s1');
  if (hero) {
    var spot = document.createElement('div');
    spot.className = 'spotlight';
    hero.insertBefore(spot, hero.firstChild);
    if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      document.addEventListener('mousemove', function(e){
        if (!e.target.closest || !e.target.closest('#s1')) return;
        var rect = hero.getBoundingClientRect();
        spot.style.setProperty('--mx', (e.clientX - rect.left) + 'px');
        spot.style.setProperty('--my', (e.clientY - rect.top) + 'px');
      });
    }
  }

  // Edición inline (solo cuando el servidor renderizó el documento con `data-cp-edit`, es decir, para quien PUEDE editar): los textos habilitados son leaves `contenteditable` de TEXTO PLANO. El guardado lo hace el editor del
  // Hub (commercial_presentations.js, sobre el mismo origen). Acá solo el comportamiento de cada leaf: pegar siempre como texto plano y sin saltos de línea (el servidor no admite saltos en ningún campo).
  if (document.documentElement.hasAttribute('data-cp-edit')) {
    document.querySelectorAll('[data-cp-field]').forEach(function(el){
      el.addEventListener('paste', function(e){
        e.preventDefault();
        var text = (e.clipboardData || window.clipboardData).getData('text/plain').replace(/\s*[\r\n]+\s*/g, ' ');
        document.execCommand('insertText', false, text);
      });
      el.addEventListener('keydown', function(e){ if (e.key === 'Enter') e.preventDefault(); });
      el.addEventListener('drop', function(e){ e.preventDefault(); });
      // Un texto dentro de un enlace («Siguiente · …») se edita sin navegar; dentro de la solapa de un caso de uso, se edita SIN que el <label> mueva el foco al radio (solo activa la solapa).
      el.addEventListener('click', function(e){
        if (el.closest('a')) e.preventDefault();
        var label = el.closest('label');
        if (label) { e.preventDefault(); if (label.control && label.control.type === 'radio') label.control.checked = true; }
      });
    });
    // Componentes por región. La biblioteca son las <template data-cp-component> que el servidor renderiza (solo composiciones REALES y registradas, cada una una sola vez); acá nunca se arma markup: se clona
    // el que ya está. El servidor revalida todo al guardar (región, componente, variante y compatibilidad de tipo).
    var tpl = {};
    document.querySelectorAll('template[data-cp-component]').forEach(function(t){ tpl[t.getAttribute('data-cp-component')] = t; });
    function syncRegion(reg){
      var occ = reg.getAttribute('data-cp-occupant'), def = reg.getAttribute('data-cp-default');
      reg.querySelectorAll('[data-cp-pick]').forEach(function(b){ b.setAttribute('aria-pressed', b.getAttribute('data-cp-pick') === occ ? 'true' : 'false'); });
      var restore = reg.querySelector('[data-cp-restore-default]'); if (restore) restore.hidden = !def || occ === def;
      var title = reg.querySelector('[data-cp-region-title]');
      var current = occ && reg.querySelector('[data-cp-pick="' + occ + '"]');
      if (title) title.textContent = current ? current.textContent : 'Región vacía';
    }
    function setOccupant(reg, ref){
      var root = reg.querySelector(':scope > [data-cp-block]'); if (root) root.remove();
      if (ref && tpl[ref]) reg.appendChild(tpl[ref].content.firstElementChild.cloneNode(true));
      reg.setAttribute('data-cp-occupant', ref && tpl[ref] ? ref : '');
      var note = reg.querySelector('[data-cp-note]'); if (note) note.hidden = !ref;       // aviso discreto: el texto de la slide no se reescribe solo
      syncRegion(reg);
      document.dispatchEvent(new CustomEvent('cp:composition-changed'));
    }
    document.querySelectorAll('[data-cp-region]').forEach(syncRegion);
    document.addEventListener('click', function(e){
      var t = e.target.closest && e.target.closest('[data-cp-pick], [data-cp-region-remove], [data-cp-restore-default]');
      if (!t) return;
      var reg = t.closest('[data-cp-region]');
      if (t.hasAttribute('data-cp-pick')) setOccupant(reg, t.getAttribute('data-cp-pick'));
      else if (t.hasAttribute('data-cp-region-remove')) setOccupant(reg, '');
      else setOccupant(reg, reg.getAttribute('data-cp-default'));
    });
    // Eliminar / restaurar una slide de la copia. La numeración («NN / total»), el riel, los «Siguiente» y el cue de la portada se recalculan sobre las slides visibles (el servidor aplica la MISMA regla al
    // renderizar la lectura). El editor del Hub escucha `cp:slides-changed` para marcar «Sin guardar».
    function pad(n){ return (n < 10 ? '0' : '') + n; }
    function renumber(){
      var secs = Array.prototype.slice.call(document.querySelectorAll('section[id^="s"]')).filter(function(s){ return /^s([2-9]|1[0-2])$/.test(s.id); });
      var vis = secs.filter(function(s){ return !s.hasAttribute('data-cp-removed'); });
      var pos = {}; vis.forEach(function(s, i){ pos[s.id] = i; });
      secs.forEach(function(s){
        var gone = s.hasAttribute('data-cp-removed');
        var kicker = s.querySelector('[data-cp-kicker-no]');
        if (kicker && !gone) kicker.textContent = pad(pos[s.id] + 1) + ' / ' + pad(vis.length) + ' · ';
        var rail = document.querySelector('nav.rail a[href="#' + s.id + '"]');
        if (rail) rail.hidden = gone;
        var link = s.querySelector('a.next-link[data-cp-next]');
        if (link) {
          var target = gone ? null : vis[pos[s.id] + 1];
          link.hidden = !target;
          if (target) {
            link.setAttribute('href', '#' + target.id);
            var own = link.querySelector('[data-cp-field]'), derived = link.querySelector('[data-cp-derived]');
            var same = target.id === link.getAttribute('data-cp-orig');
            own.hidden = !same; derived.hidden = same;
            if (!same) { var nav = target.querySelector('.topic-kicker [data-cp-field]'); derived.textContent = nav ? nav.textContent : ''; }
          }
        }
      });
      var cue = document.querySelector('#s1 a.scroll-cue');
      if (cue) { cue.hidden = !vis.length; if (vis.length) cue.setAttribute('href', '#' + vis[0].id); }
    }
    renumber();
    document.addEventListener('input', renumber);
    document.addEventListener('click', function(e){
      var btn = e.target.closest && e.target.closest('[data-cp-remove], [data-cp-restore]');
      if (!btn) return;
      var sec = btn.closest('section');
      if (btn.hasAttribute('data-cp-remove')) sec.setAttribute('data-cp-removed', ''); else sec.removeAttribute('data-cp-removed');
      renumber();
      document.dispatchEvent(new CustomEvent('cp:slides-changed'));
    });
    // El nombre de una sección aparece en dos lugares (etiqueta superior y riel; el título del pilar, también en su detalle): se edita en uno solo y el otro lo refleja en vivo.
    document.addEventListener('input', function(e){
      var src = e.target.closest && e.target.closest('[data-cp-field]');
      if (!src) return;
      var text = src.textContent.replace(/ /g, ' ');
      document.querySelectorAll('[data-cp-mirror="' + src.getAttribute('data-cp-field') + '"]').forEach(function(m){ m.textContent = text; });
    });
  }

  // Tema: contrato ÚNICO del Hub (hub_theme.js: preferencia `dw-content-theme`, clase `content-theme-*` en <html>). La presentación SIGUE al Hub (mensaje `danwor-theme` del visor embebido
  // y evento `storage` entre pestañas) y, abierta en pestaña propia, muestra el toggle en el extremo derecho del header. Embebida el header no se renderiza: el toggle es el del Hub.
  if (window.dwTheme) {
    window.dwTheme.apply(window.dwTheme.current());
    window.dwTheme.listen();
    var themeToggle = document.querySelector('[data-dw-theme-toggle]');
    if (themeToggle) themeToggle.addEventListener('click', function(){
      window.dwTheme.set(window.dwTheme.current() === 'light' ? 'dark' : 'light');
    });
  }

  var grain = document.createElement('div');
  grain.className = 'grain-overlay';
  document.body.appendChild(grain);
})();

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
    // Componentes por región. La biblioteca son las <template data-cp-component> que el servidor renderiza (solo composiciones REALES y registradas, cada una una sola vez): de ahí salen tanto el componente que se
    // inserta como su vista previa en el diálogo; acá nunca se arma markup, solo se clona el que ya está. La ✕ del bloque ABRE el diálogo (no quita nada); recién «Reemplazar/Agregar/Quitar» modifican la
    // composición, y el servidor revalida todo al guardar (región, componente, variante y compatibilidad de tipo).
    var tpl = {};
    document.querySelectorAll('template[data-cp-component]').forEach(function(t){ tpl[t.getAttribute('data-cp-component')] = t; });
    var dlg = document.getElementById('cp-addon-dialog'), current = null;
    function syncRegion(reg){
      var occ = reg.getAttribute('data-cp-occupant'), def = reg.getAttribute('data-cp-default');
      var restore = reg.querySelector('[data-cp-restore-default]'); if (restore) restore.hidden = !def || occ === def;
    }
    function setOccupant(reg, ref){
      var root = reg.querySelector(':scope > [data-cp-block]'); if (root) root.remove();
      if (ref && tpl[ref]) reg.appendChild(tpl[ref].content.firstElementChild.cloneNode(true));
      reg.setAttribute('data-cp-occupant', ref && tpl[ref] ? ref : '');
      syncRegion(reg);
      document.dispatchEvent(new CustomEvent('cp:composition-changed'));
    }
    function selectedRef(){ var r = dlg.querySelector('input[name="cp-addon-ref"]:checked'); return r ? r.value : ''; }
    var STAGE_W = 1100;                                                                // ancho natural con el que se maqueta el componente en la vista previa (el real vive en un contenedor de 1180)
    function renderPreview(ref){
      var box = dlg.querySelector('[data-cp-addon-preview]');
      box.replaceChildren();
      if (!ref || !tpl[ref]) return;
      var sizer = document.createElement('div'), stage = document.createElement('div');
      sizer.className = 'cp-addon-sizer'; stage.className = 'cp-addon-stage';
      stage.appendChild(tpl[ref].content.firstElementChild.cloneNode(true));
      sizer.appendChild(stage); box.appendChild(sizer);
      var k = Math.min(1, (box.clientWidth - 26) / STAGE_W);                              // se reduce para entrar; el layout es el real a 1100 px
      stage.style.transform = 'scale(' + k + ')';
      sizer.style.width = Math.ceil(STAGE_W * k) + 'px'; sizer.style.height = Math.ceil(stage.offsetHeight * k) + 'px';
    }
    function syncDialog(){
      var occ = current.getAttribute('data-cp-occupant'), ref = selectedRef();
      var apply = dlg.querySelector('[data-cp-addon-apply]');
      apply.textContent = occ ? 'Reemplazar' : 'Agregar';
      apply.disabled = !ref || ref === occ;
    }
    function openDialog(reg){
      current = reg;
      var occ = reg.getAttribute('data-cp-occupant'), def = reg.getAttribute('data-cp-default'), type = reg.getAttribute('data-cp-region-type');
      dlg.querySelector('[data-cp-addon-title]').textContent = occ ? 'Componente de esta región' : 'Agregar componente';
      dlg.querySelectorAll('[data-cp-opt]').forEach(function(o){
        var input = o.querySelector('input');
        o.hidden = o.getAttribute('data-cp-opt-type') !== type;                           // solo las composiciones compatibles con el TIPO de región
        input.checked = !!occ && input.value === occ;
        o.querySelector('[data-cp-opt-original]').hidden = !def || input.value !== def;
      });
      dlg.querySelector('[data-cp-addon-remove]').hidden = !occ;
      syncDialog();
      dlg.showModal();
      renderPreview(occ);                                                                  // con el diálogo abierto: hace falta su ancho para escalar
    }
    document.querySelectorAll('[data-cp-region]').forEach(syncRegion);
    if (dlg) {
      dlg.addEventListener('change', function(e){
        if (!e.target.closest || !e.target.closest('input[name="cp-addon-ref"]')) return;
        renderPreview(selectedRef());                                                      // seleccionar solo cambia la vista previa
        syncDialog();
      });
      dlg.querySelector('[data-cp-addon-apply]').addEventListener('click', function(){
        var ref = selectedRef();
        if (ref) setOccupant(current, ref);
        dlg.close();
      });
      dlg.querySelector('[data-cp-addon-remove]').addEventListener('click', function(){ setOccupant(current, ''); dlg.close(); });
      dlg.addEventListener('close', function(){ dlg.querySelector('[data-cp-addon-preview]').replaceChildren(); current = null; });
    }
    document.addEventListener('click', function(e){
      var t = e.target.closest && e.target.closest('[data-cp-region-manage], [data-cp-restore-default]');
      if (!t) return;
      var reg = t.closest('[data-cp-region]');
      if (t.hasAttribute('data-cp-restore-default')) setOccupant(reg, reg.getAttribute('data-cp-default'));
      else openDialog(reg);
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

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

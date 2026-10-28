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
      document.querySelectorAll('nav a[href^="#s"]').forEach(function(a){
        a.classList.add('nav-dot');
        a.classList.toggle('is-active', a.getAttribute('href') === '#' + current.id);
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

  if (!reduceMotion) {
    var lastTilt = null;
    document.addEventListener('mousemove', function(e){
      var el = e.target.closest ? e.target.closest('[data-tilt]') : null;
      if (lastTilt && lastTilt !== el) {
        lastTilt.style.setProperty('--rx', '0deg');
        lastTilt.style.setProperty('--ry', '0deg');
      }
      lastTilt = el;
      if (!el) return;
      var rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      var px = (e.clientX - rect.left) / rect.width;
      var py = (e.clientY - rect.top) / rect.height;
      var clamp = function(v){ return Math.max(-8, Math.min(8, v)); };
      el.style.setProperty('--ry', clamp((px - 0.5) * 8).toFixed(2) + 'deg');
      el.style.setProperty('--rx', clamp((0.5 - py) * 8).toFixed(2) + 'deg');
    });

    var lastMagnet = null;
    document.addEventListener('mousemove', function(e){
      var el = e.target.closest ? e.target.closest('a') : null;
      if (el && parseFloat(getComputedStyle(el).borderRadius) < 100) el = null;
      if (lastMagnet && lastMagnet !== el) lastMagnet.style.transform = '';
      lastMagnet = el;
      if (!el) return;
      var rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      var mx = Math.max(-40, Math.min(40, e.clientX - rect.left - rect.width / 2));
      var my = Math.max(-40, Math.min(40, e.clientY - rect.top - rect.height / 2));
      el.style.transform = 'translate(' + (mx * 0.16).toFixed(1) + 'px,' + (my * 0.3).toFixed(1) + 'px)';
    });

    var lastHero = null;
    document.addEventListener('mousemove', function(e){
      var hero = e.target.closest ? e.target.closest('#s1') : null;
      if (!hero) {
        if (lastHero) {
          var oldSpot = lastHero.querySelector(':scope > .spotlight');
          if (oldSpot) oldSpot.style.opacity = '0';
        }
        lastHero = null;
        return;
      }
      lastHero = hero;
      var spot = hero.querySelector(':scope > .spotlight');
      if (!spot) {
        spot = document.createElement('div');
        spot.className = 'spotlight';
        hero.insertBefore(spot, hero.firstChild);
      }
      var rect = hero.getBoundingClientRect();
      spot.style.setProperty('--mx', (e.clientX - rect.left) + 'px');
      spot.style.setProperty('--my', (e.clientY - rect.top) + 'px');
      spot.style.opacity = '1';
    });
  }

  var grain = document.createElement('div');
  grain.className = 'grain-overlay';
  document.body.appendChild(grain);
})();

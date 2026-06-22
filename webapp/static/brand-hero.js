/* brand-hero.js — Portado de E:\Joe\Metodologia\web\main.js: la MISMA red de
   partículas del hero (initParticles) + el gradiente reactivo al mouse. Único
   cambio: el dorado rgba(184,150,12) → rgba(231,176,8) (#E7B008). */
(function () {
  var fineHover = matchMedia("(hover: hover) and (pointer: fine)").matches;

  // --- Gradiente reactivo al mouse (actualiza --mx/--my, como en el sitio) ---
  function initMouseGradient() {
    if (!fineHover) return;
    var root = document.documentElement;
    window.addEventListener("mousemove", function (e) {
      root.style.setProperty("--mx", (e.clientX / window.innerWidth * 100) + "%");
      root.style.setProperty("--my", (e.clientY / window.innerHeight * 100) + "%");
    }, { passive: true });
  }

  // --- Red de partículas (verbatim del hero del sitio de marca) ---
  function initParticles() {
    var canvas = document.querySelector(".hero-particles");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var W, H, dpr;
    var nodes = [];
    var COUNT = 52;
    var MAX_D = 138;
    var mx = -999, my = -999;

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = canvas.offsetWidth;
      H = canvas.offsetHeight;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    window.addEventListener("resize", resize, { passive: true });
    resize();

    for (var i = 0; i < COUNT; i++) {
      nodes.push({
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.28,
        vy: (Math.random() - 0.5) * 0.28,
        r: 1 + Math.random() * 1.2
      });
    }

    if (fineHover) {
      window.addEventListener("mousemove", function (e) {
        var rect = canvas.getBoundingClientRect();
        mx = e.clientX - rect.left;
        my = e.clientY - rect.top;
      }, { passive: true });
    }

    function frame() {
      ctx.clearRect(0, 0, W, H);

      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        var dx = mx - n.x, dy = my - n.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < 90000) {
          var d = Math.sqrt(d2);
          n.vx += (dx / d) * 0.012;
          n.vy += (dy / d) * 0.012;
        }
        var sp = n.vx * n.vx + n.vy * n.vy;
        if (sp > 0.64) { var f = 0.8 / Math.sqrt(sp); n.vx *= f; n.vy *= f; }
        n.x += n.vx; n.y += n.vy;
        if (n.x < 0) n.x = W; else if (n.x > W) n.x = 0;
        if (n.y < 0) n.y = H; else if (n.y > H) n.y = 0;

        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, 6.2832);
        ctx.fillStyle = "rgba(231,176,8,0.65)";
        ctx.fill();
      }

      for (var i = 0; i < nodes.length - 1; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var dx = nodes[i].x - nodes[j].x;
          var dy = nodes[i].y - nodes[j].y;
          var d = Math.sqrt(dx * dx + dy * dy);
          if (d < MAX_D) {
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.strokeStyle = "rgba(231,176,8," + ((1 - d / MAX_D) * 0.22) + ")";
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }

      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // --- Scroll del intro → baja a la pantalla de acceso (como el sitio) ---
  function initScrollCue() {
    var acceso = document.getElementById("acceso");
    var cue = document.querySelector(".hero-scroll");
    if (cue && acceso) {
      cue.addEventListener("click", function () {
        acceso.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }

  // El login Microsoft (window.loginWithMicrosoft) lo maneja auth.js.

  function boot() { initMouseGradient(); initParticles(); initScrollCue(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();

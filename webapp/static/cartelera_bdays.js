/* Cartelera Virtual · Tile de cumpleaños de Inicio: muestra tantas filas como entran COMPLETAS en el alto del tile (que lo fija el tile de
   bienvenida: `contain:size`, así el contenido nunca lo estira ni genera scroll). Prioridad por orden: primero los de hoy, después los
   próximos; si un grupo queda sin filas visibles, se oculta su título. Recalcula al redimensionar (sin ciclos: el alto no depende del contenido). */
(function () {
  'use strict';
  function init(tile) {
    var rows = Array.prototype.slice.call(tile.querySelectorAll('[data-brow]'));
    var groups = Array.prototype.slice.call(tile.querySelectorAll('[data-bgroup]'));
    if (!rows.length) return;
    tile.classList.add('bday-tile--fit');
    function fit() {
      rows.forEach(function (r) { r.hidden = false; });
      groups.forEach(function (g) { g.hidden = false; });
      var cs = getComputedStyle(tile), limit = tile.getBoundingClientRect().bottom - parseFloat(cs.paddingBottom || 0) + 0.5;
      if (cs.contain === 'none' || cs.contain === '') {                              // celular (apilado): sin alto de referencia → hoy + 5 próximos
        var next = 0;
        rows.forEach(function (r) { if (r.classList.contains('bday-next')) { next++; r.hidden = next > 5; } });
        groups.forEach(function (g) { g.hidden = !g.querySelector('[data-brow]:not([hidden])'); });
        return;
      }
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].getBoundingClientRect().bottom > limit) { for (var j = i; j < rows.length; j++) rows[j].hidden = true; break; }
      }
      groups.forEach(function (g) { g.hidden = !g.querySelector('[data-brow]:not([hidden])'); });
    }
    var raf = 0;
    function schedule() { if (!raf) raf = requestAnimationFrame(function () { raf = 0; fit(); }); }
    if (window.ResizeObserver) new ResizeObserver(schedule).observe(tile); else window.addEventListener('resize', schedule);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
    fit();
  }
  function boot() { document.querySelectorAll('[data-bfit]').forEach(init); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();

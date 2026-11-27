/* Cartelera Virtual · Galería de Nosotros: misma experiencia que la app original — portada del álbum → miniaturas → visor con
   anterior/siguiente (teclado: ←/→ y Escape). Usa `.modal`, `.gal-thumbs`, `.gal-th` y `.gal-view` del portal y la capa `#layer`.
   Las imágenes salen de la ruta autorizada del Hub (`/cartelera/media/<id>/thumb|display`). */
(function () {
  'use strict';
  var titles = JSON.parse((document.getElementById('ct-gallery') || {}).textContent || '[]');
  var albums = JSON.parse((document.getElementById('ct-gallery-photos') || {}).textContent || '[]');
  var layer = document.getElementById('layer');
  if (!layer) return;
  var src = function (id, v) { return '/cartelera/media/' + encodeURIComponent(id) + '/' + v; };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var state = null;

  function close() { layer.innerHTML = ''; state = null; document.removeEventListener('keydown', onKey); }
  function frame(title, body) {
    layer.innerHTML = '<div class="modal-bg" data-gal-bg><div class="modal modal-wide" role="dialog" aria-modal="true" aria-label="' + esc(title) + '">' +
      '<div class="modal-h"><h2>' + esc(title) + '</h2><button type="button" class="icon-btn" data-gal-close aria-label="Cerrar">✕</button></div>' + body + '</div></div>';
  }
  function thumbs(a) {
    var photos = albums[a] || [];
    state = { a: a, i: null };
    frame(titles[a] || 'Álbum', '<div class="gal-thumbs">' + photos.map(function (p, i) {
      return '<button type="button" class="gal-th" data-gal-photo="' + i + '" aria-label="Foto ' + (i + 1) + ' de ' + photos.length + '"><img src="' + src(p.id, 'thumb') + '" alt="' + esc(p.caption || '') + '" loading="lazy"></button>';
    }).join('') + '</div>');
  }
  function view(i) {
    var photos = albums[state.a] || [];
    i = Math.max(0, Math.min(photos.length - 1, i)); state.i = i;
    var p = photos[i];
    frame((titles[state.a] || 'Álbum') + ' · ' + (i + 1) + ' de ' + photos.length,
      '<div class="gal-view"><button type="button" class="icon-btn gal-nav" data-gal-step="-1" aria-label="Anterior"' + (i === 0 ? ' disabled' : '') + '>‹</button>' +
      '<img src="' + src(p.id, 'display') + '" alt="' + esc(p.caption || '') + '">' +
      '<button type="button" class="icon-btn gal-nav" data-gal-step="1" aria-label="Siguiente"' + (i === photos.length - 1 ? ' disabled' : '') + '>›</button></div>' +
      (p.caption ? '<p class="muted" style="margin:0;text-align:center">' + esc(p.caption) + '</p>' : '') +
      '<div class="row" style="justify-content:center"><button type="button" class="btn sm" data-gal-back>Ver todas las fotos</button></div>');
  }
  function onKey(e) {
    if (!state) return;
    if (e.key === 'Escape') { e.preventDefault(); if (state.i == null) close(); else thumbs(state.a); }
    else if (state.i != null && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); view(state.i + (e.key === 'ArrowLeft' ? -1 : 1)); }
  }
  document.addEventListener('click', function (e) {
    var t = e.target;
    var open = t.closest && t.closest('[data-gal]');
    if (open) { thumbs(+open.getAttribute('data-gal')); document.addEventListener('keydown', onKey); return; }
    if (!state) return;
    if (t.closest('[data-gal-close]') || t.hasAttribute('data-gal-bg')) { close(); return; }
    var ph = t.closest('[data-gal-photo]'); if (ph) { view(+ph.getAttribute('data-gal-photo')); return; }
    var st = t.closest('[data-gal-step]'); if (st && !st.disabled) { view(state.i + (+st.getAttribute('data-gal-step'))); return; }
    if (t.closest('[data-gal-back]')) thumbs(state.a);
  });
})();

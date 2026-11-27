/* Hub · campana del header (Centro de Notificaciones): bandeja unificada `/notificaciones`, contador de no leídas COMPARTIDO con la campana
   del portal de Cartelera, abrir = marcar leída y navegar al contexto (`/notificaciones/abrir/<id>`), «Marcar todas como leídas».
   Reutiliza el desplegable del topbar (`.workspace-switch` / `.workspace-switch-menu`) y el botón `.theme-toggle`; apertura y cierre los gobierna `hub_topbar_menus.js`. Sin sondeo:
   se actualiza al cargar la página y al abrirla. */
(function () {
  'use strict';
  var root = document.querySelector('[data-dw-notif]');
  if (!root) return;
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var BELL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>';

  function render(data) {
    var open = root.querySelector('details') && root.querySelector('details').open;
    var n = data.unread || 0;
    root.innerHTML =
      '<details class="workspace-switch dw-notif"' + (open ? ' open' : '') + '>' +
        '<summary class="theme-toggle dw-notif-btn" aria-label="Notificaciones' + (n ? ', ' + n + ' sin leer' : '') + '" title="Notificaciones">' + BELL +
          (n ? '<span class="dw-notif-badge">' + (n > 9 ? '9+' : n) + '</span>' : '') + '</summary>' +
        '<div class="workspace-switch-menu dw-notif-menu">' +
          '<div class="dw-notif-head"><b>Notificaciones</b>' + (n ? '<button type="button" class="dw-notif-all" data-dw-notif-all>Marcar todas como leídas</button>' : '') + '</div>' +
          (data.items && data.items.length ? data.items.map(function (x) {
            return '<a href="' + esc(x.href) + '" class="dw-notif-item' + (x.unread ? ' is-unread' : '') + '"><span>' + esc(x.text) + '</span>' +
                   '<small>' + esc(x.source_label) + ' · ' + esc(x.when) + '</small></a>';
          }).join('') : '<p class="dashboard-empty__hint dw-notif-empty">No tenés notificaciones.</p>') +
        '</div>' +
      '</details>';
  }
  function load() {
    return fetch('/notificaciones', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d) render(d); });
  }
  root.addEventListener('click', function (e) {
    var all = e.target.closest && e.target.closest('[data-dw-notif-all]');
    if (!all) return;
    e.preventDefault(); all.disabled = true;
    fetch('/notificaciones/todas', { method: 'POST', credentials: 'same-origin' }).then(load).catch(function () { all.disabled = false; });
  });
  root.addEventListener('toggle', function (e) { if (e.target.open) load(); }, true);       // al abrirla, estado fresco
  load();
})();

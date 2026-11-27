/* Hub · perfil del usuario (transversal): menú del header con la identidad corporativa (Entra ID, solo lectura) y la foto efectiva
   (personalizada → corporativa → iniciales). Cargar/sustituir/quitar la foto PERSONALIZADA propia. Una vez por día sincroniza la foto
   corporativa PROPIA con el token delegado ya consentido (`User.Read`); los demás la ven sin llamadas a Graph.
   Reutiliza el desplegable del topbar (`.workspace-switch` / `.workspace-switch-menu`) y el botón `.theme-toggle`; apertura y cierre los gobierna `hub_topbar_menus.js`.
   «Cerrar sesión» es una acción del contenedor de usuario: se ofrece donde existe el logout del chrome del Hub (`window.dwLogout`, app.js); el portal
   de Cartelera (presentación limpia, sin app.js) conserva su menú sin esa acción. */
(function () {
  'use strict';
  var root = document.querySelector('[data-dw-profile]');
  if (!root) return;
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var face = function (me, cls) {
    return me.avatar ? '<span class="dw-avatar ' + cls + '"><img src="' + esc(me.avatar) + '" alt=""></span>'
                     : '<span class="dw-avatar ' + cls + '" aria-hidden="true">' + esc(me.initials) + '</span>';
  };
  var LABEL = { custom: 'Foto personalizada del Hub', corporate: 'Foto de tu cuenta Danwor (Microsoft 365)' };

  function render(me, msg, err) {
    var open = root.querySelector('details') && root.querySelector('details').open;
    root.innerHTML =
      '<details class="workspace-switch dw-profile"' + (open ? ' open' : '') + '>' +
        '<summary class="theme-toggle dw-profile-btn" aria-label="Tu perfil" title="Tu perfil">' + face(me, 'dw-avatar--sm') + '</summary>' +
        '<div class="workspace-switch-menu dw-profile-menu">' +
          '<div class="dw-profile-id">' + face(me, 'dw-avatar--lg') + '<div><b>' + esc(me.name) + '</b><span>' + esc(me.email) + '</span>' +
            '<small>' + esc(LABEL[me.source] || 'Sin foto: se muestran tus iniciales') + '</small></div></div>' +
          '<button type="button" class="btn-secondary" data-dw-photo="upload">' + (me.has_custom ? 'Cambiar foto' : 'Subir foto personalizada') + '</button>' +
          (me.has_custom ? '<button type="button" class="btn-secondary" data-dw-photo="remove">Quitar foto personalizada</button>' : '') +
          '<p class="dashboard-empty__hint">La foto personalizada se usa solo en el Hub; no cambia tu cuenta de Microsoft 365. Tu nombre y correo los administra Microsoft 365.</p>' +
          (msg ? '<p class="dashboard-empty__hint" role="status">' + esc(msg) + '</p>' : '') +
          (err ? '<p class="feedback-error" role="alert">' + esc(err) + '</p>' : '') +
          (typeof window.dwLogout === 'function' ? '<div class="dw-profile-logout"><button type="button" class="btn-secondary" data-dw-logout>Cerrar sesión</button></div>' : '') +
        '</div>' +
      '</details>';
  }
  function load() { return fetch('/perfil/yo', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }); }
  function post(url, body, json) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: body, headers: json ? { 'Content-Type': 'application/json' } : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || 'No se pudo completar la acción.'); return j; }); });
  }
  root.addEventListener('click', function (e) {
    var out = e.target.closest && e.target.closest('[data-dw-logout]');
    if (out && typeof window.dwLogout === 'function') { out.disabled = true; window.dwLogout(); return; }
    var b = e.target.closest && e.target.closest('[data-dw-photo]');
    if (!b || b.disabled) return;
    if (b.getAttribute('data-dw-photo') === 'remove') {
      b.disabled = true;
      post('/perfil/foto/quitar').then(function (me) { render(me, 'Volviste a tu foto corporativa o a tus iniciales.'); }).catch(function (x) { b.disabled = false; load().then(function (me) { render(me, '', x.message); }); });
      return;
    }
    var input = document.createElement('input'); input.type = 'file'; input.accept = 'image/jpeg,image/png,image/webp';
    input.addEventListener('change', function () {
      var f = input.files && input.files[0]; if (!f) return;
      b.disabled = true; b.textContent = 'Subiendo…';
      var fd = new FormData(); fd.append('file', f);
      post('/perfil/foto', fd).then(function (me) { render(me, 'Foto actualizada.'); }).catch(function (x) { load().then(function (me) { render(me, '', x.message); }); });
    });
    input.click();
  });
  load().then(function (me) {
    if (!me) return;
    render(me);
    // Foto corporativa propia: como mucho una vez por día y por usuario, sin bloquear la página.
    var day = new Date().toISOString().slice(0, 10), mark = 'dw-avatar-sync:' + me.email;
    var last = ''; try { last = localStorage.getItem(mark) || ''; } catch (e) { /* almacenamiento no disponible */ }
    if (last === day || typeof window.dwGraphToken !== 'function') return;
    Promise.resolve(window.dwGraphToken()).then(function (token) {
      if (!token) return;
      return post('/perfil/foto/corporativa', JSON.stringify({ graph_token: token }), true).then(function (fresh) {
        try { localStorage.setItem(mark, day); } catch (e) { /* sin persistencia local */ }
        if (fresh.avatar !== me.avatar) render(fresh);
      });
    }).catch(function () { /* sin token o sin red: se reintenta en otra visita */ });
  });
})();

/* Hub · desplegables del topbar: UN solo mecanismo para todos los `<details class="workspace-switch">` de `.header-tools` (selector de
   espacios, Perfil y Avatar, Centro de Notificaciones y cualquier otro que reutilice el mismo primitivo). Delegado en `document`, así que
   también gobierna los desplegables que `hub_profile.js` / `hub_notifications.js` re-renderizan.
     - click sobre el control: abre; segundo click sobre el mismo: cierra (comportamiento nativo de `<details>`);
     - abrir uno cierra el que estuviera abierto: nunca hay más de un desplegable activo;
     - click fuera o Escape: cierra el activo (con Escape el foco vuelve a su control);
     - click dentro del panel: lo preserva (las acciones que navegan lo cierran solas al cambiar de página). */
(function () {
  'use strict';
  var SEL = '.header-tools details.workspace-switch';
  function others(keep) {
    return Array.prototype.filter.call(document.querySelectorAll(SEL + '[open]'), function (d) { return d !== keep; });
  }
  function closeOthers(keep) { others(keep).forEach(function (d) { d.open = false; }); }

  document.addEventListener('toggle', function (e) {                    // `toggle` no burbujea: se escucha en captura
    var d = e.target;
    if (d.open && d.matches && d.matches(SEL)) closeOthers(d);
  }, true);
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.isConnected) return;                                    // el panel se re-renderizó durante su propia acción: sigue siendo «dentro»
    closeOthers(t.closest ? t.closest(SEL) : null);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    others(null).forEach(function (d) {
      var hadFocus = d.contains(document.activeElement);
      d.open = false;
      var s = d.querySelector(':scope > summary');
      if (hadFocus && s) s.focus();
    });
  });
})();

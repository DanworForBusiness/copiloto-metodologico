/* Tokens stay in the request context; MSAL interaction belongs to hub_auth.js. */
(function () {
  const resolved = new WeakMap();
  const needsWrite = elt => !!elt?.closest('[data-solicitudes-write-token]');
  document.addEventListener('htmx:confirm', event => {
    const elt = event.detail?.elt;
    if (!needsWrite(elt)) return;
    event.preventDefault();
    window.dwSolicitudesWriteToken({interactive: false}).then(token => {
      resolved.set(elt, token);
      try { event.detail.issueRequest(true); } catch (_) { resolved.delete(elt); }
    });
  });
  document.addEventListener('htmx:configRequest', event => {
    const elt = event.detail?.elt;
    if (!needsWrite(elt)) return;
    event.detail.parameters['graph_token'] = resolved.get(elt) || '';
    resolved.delete(elt);
  });
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-solicitudes-authorize]');
    if (!button) return;
    button.disabled = true;
    const status = button.parentElement.querySelector('[data-solicitudes-authorize-status]');
    try {
      const token = await window.dwSolicitudesWriteToken({interactive: true});
      status.textContent = token ? 'Autorización recuperada. Podés volver a enviar el formulario.' : 'No se pudo autorizar la escritura. Conservá tu borrador y consultá los permisos de Microsoft.';
    } finally { button.disabled = false; }
  });
})();

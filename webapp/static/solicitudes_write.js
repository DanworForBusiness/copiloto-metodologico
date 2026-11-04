/* Tokens stay in the request context; MSAL interaction belongs to hub_auth.js. */
(function () {
  document.addEventListener('htmx:afterSettle', event => {
    const target = event.detail?.target;
    if (!target || target.id !== 'solicitudes-save-result') return;
    const feedback = target.querySelector('[data-solicitudes-saved]');
    if (!feedback) return;
    const rect = feedback.getBoundingClientRect();
    const topbar = document.querySelector('.topbar');
    const top = Math.max(0, topbar?.getBoundingClientRect().bottom || 0);
    if (rect.top >= top && rect.bottom <= window.innerHeight) return;
    feedback.scrollIntoView({
      block: 'center',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    });
  });
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

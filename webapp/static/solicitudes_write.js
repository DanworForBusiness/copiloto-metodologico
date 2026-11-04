/* Tokens stay in the request context; MSAL interaction belongs to hub_auth.js. */
(function () {
  document.addEventListener('solicitudes-deleted', event => {
    if (document.getElementById('solicitudes-list-result') && event.detail?.url === '/operaciones/solicitudes') {
      window.location.assign('/operaciones/solicitudes');
    }
  });
  // Keep the submitted observation stable while Graph is in flight; errors
  // unlock the same controls without replacing their draft.
  const lockedDrafts = new WeakMap();
  document.addEventListener('htmx:beforeRequest', event => {
    const elt = event.detail?.elt;
    if (!elt?.matches('form[data-solicitudes-write-token]')) return;
    const fields = Array.from(elt.querySelectorAll('[data-new-observation], [name=reason]'));
    lockedDrafts.set(elt, fields.map(field => [field, field.readOnly]));
    fields.forEach(field => { field.readOnly = true; });
  });
  document.addEventListener('htmx:afterRequest', event => {
    const elt = event.detail?.elt;
    for (const [field, previous] of lockedDrafts.get(elt) || []) field.readOnly = previous;
    if (elt) lockedDrafts.delete(elt);
  });
  document.addEventListener('htmx:afterSwap', event => {
    const target = event.detail?.target;
    if (target?.id !== 'solicitudes-save-result') return;
    const saved = target.querySelector('[data-solicitudes-saved]');
    if (saved) document.querySelectorAll('[data-new-observation]').forEach(field => { field.value = ''; });
    if (target.querySelector('[data-solicitudes-rejection]')) {
      const dialog = document.getElementById('solicitudes-reject-dialog');
      if (!dialog) return;
      if (saved) { dialog.close(); dialog.querySelector('[name=reason]').value = ''; }
      else dialog.querySelector('[data-rejection-feedback]').innerHTML = target.innerHTML;
    }
  });
  document.addEventListener('htmx:afterSettle', event => {
    const target = event.detail?.target;
    if (!target || !['solicitudes-save-result', 'solicitudes-list-result'].includes(target.id)) return;
    const feedback = target.querySelector(target.id === 'solicitudes-list-result' ? '.callout' : '[data-solicitudes-saved]');
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
  const preparing = new WeakSet();
  const needsWrite = elt => !!elt?.closest('[data-solicitudes-write-token]');
  document.addEventListener('htmx:confirm', async event => {
    const elt = event.detail?.elt;
    if (!needsWrite(elt)) return;
    event.preventDefault();
    if (preparing.has(elt)) return;
    preparing.add(elt);
    try {
    const form = elt.closest('form[data-solicitudes-admission]');
    let admissionConfirmed = false;
    if (elt === form && form.dataset.solicitudesAdmission === 'Solicitado') {
      const hasDate = ['sol-planned', 'sol-actual'].some(id => {
        const field = form.querySelector('#' + id);
        return field && !!field.value;
      });
      const status = form.querySelector('#sol-status');
      const committed = status && ['Planificado', 'En curso', 'Finalizado'].includes(status.value);
      if (hasDate || committed) {
        admissionConfirmed = await dwConfirm({title: 'Aprobar solicitud',
          message: 'Esta solicitud tiene actividad comprometida por Arquitectura, pero su Funnel continúa en Solicitado. Al guardar, se actualizará a Aprobado para Arquitectura. ¿Continuar?',
          confirmLabel: 'Confirmar y guardar', cancelLabel: 'Cancelar'});
        if (!admissionConfirmed) return;
      }
    }
    const token = await window.dwSolicitudesWriteToken({interactive: false});
    resolved.set(elt, {token, admissionConfirmed});
    try { event.detail.issueRequest(true); } catch (_) { resolved.delete(elt); }
    } finally { preparing.delete(elt); }
  });
  document.addEventListener('htmx:configRequest', event => {
    const elt = event.detail?.elt;
    if (!needsWrite(elt)) return;
    const acquired = resolved.get(elt);
    event.detail.parameters['graph_token'] = acquired?.token || '';
    if (acquired?.admissionConfirmed) event.detail.parameters['confirm_admission'] = 'yes';
    resolved.delete(elt);
  });
  document.addEventListener('click', async event => {
    const open = event.target.closest('[data-solicitudes-reject-open]');
    if (open) {
      const form = open.closest('form');
      if (window.Alpine.$data(form).dirty) {
        await dwConfirm({title: 'Hay cambios sin guardar',
          message: 'Guardá tus cambios o recargá la solicitud para descartarlos antes de rechazar. No se guardó ni descartó tu borrador.',
          confirmLabel: 'Entendido'});
        return;
      }
      document.getElementById('solicitudes-reject-dialog')?.showModal();
      return;
    }
    if (event.target.closest('[data-solicitudes-reject-cancel]')) {
      document.getElementById('solicitudes-reject-dialog')?.close();
      return;
    }
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

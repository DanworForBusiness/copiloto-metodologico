/* Tokens stay in the request context; MSAL interaction belongs to hub_auth.js. */
(function () {
  function rejectionFailure(elt, message, tone = 'punch') {
    const form = elt?.closest('form[data-solicitudes-write-token][hx-post$="/rechazar"]');
    const target = form?.querySelector('[data-rejection-feedback]');
    if (!target) return;
    const block = document.createElement('div');
    block.className = 'callout callout--' + tone + ' callout--feedback';
    block.setAttribute('role', 'alert');
    const copy = document.createElement('p');
    copy.className = 'field-hint';
    copy.textContent = message;
    block.appendChild(copy);
    const path = form.getAttribute('hx-post');
    if (/^\/operaciones\/solicitudes\/\d+\/rechazar$/.test(path)) {
      const link = document.createElement('a');
      link.href = path.replace(/\/rechazar$/, '/editar');
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Consultar el estado vigente en otra pestaña';
      block.appendChild(link);
    }
    target.replaceChildren(block);
  }
  document.addEventListener('htmx:responseError', event => {
    const code = event.detail?.xhr?.status;
    const message = code === 401 ? 'La sesión o autorización venció. Conservá la justificación y recuperá el acceso antes de volver a intentar.'
      : code === 403 ? 'No tenés permiso para rechazar esta solicitud. La justificación se conserva.'
      : 'No pudimos confirmar el resultado del rechazo (HTTP ' + code + '). La justificación se conserva. Consultá el estado vigente antes de volver a intentar.';
    rejectionFailure(event.detail?.elt, message, code === 401 || code === 403 ? 'warn' : 'punch');
  });
  for (const name of ['htmx:sendError', 'htmx:timeout', 'htmx:sendAbort']) {
    document.addEventListener(name, event => rejectionFailure(event.detail?.elt,
      'La comunicación se interrumpió y no pudimos confirmar el resultado del rechazo. La justificación se conserva. Consultá el estado vigente antes de volver a intentar; no se reenvió automáticamente.'));
  }
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
    const source = event.detail?.requestConfig?.elt;
    if (source?.closest('form[data-solicitudes-write-token][hx-post$="/rechazar"]') &&
        !target.querySelector('[data-solicitudes-rejection]')) {
      rejectionFailure(source, 'La respuesta no permitió verificar el rechazo. La justificación se conserva; consultá el estado vigente antes de volver a intentar.');
      return;
    }
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
    try { event.detail.issueRequest(true); } catch (_) {
      resolved.delete(elt);
      rejectionFailure(elt, 'No pudimos confirmar el resultado de la petición. La justificación se conserva; consultá el estado vigente antes de volver a intentar.');
    }
    } catch (_) {
      rejectionFailure(elt, 'No se pudo iniciar el rechazo con la autorización de Microsoft. La justificación se conserva; no se envió la petición de rechazo.', 'warn');
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
      status.className = 'callout callout--feedback callout--' + (token ? 'success' : 'warn');
      status.textContent = token ? 'Autorización recuperada. Podés volver a enviar el formulario.' : 'No se pudo autorizar la escritura. Conservá tu borrador y consultá los permisos de Microsoft.';
    } finally { button.disabled = false; }
  });
})();

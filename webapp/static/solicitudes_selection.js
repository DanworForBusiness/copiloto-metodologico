/* Solicitudes adapter for the shared CCHH selection primitives. IDs only. */
(function () {
  let store = null, scope = '';
  const root = () => document.querySelector('[data-sol-selection-bar]');
  function storage() { try { return window.sessionStorage; } catch (_) { return null; } }
  function sync() {
    const bar = root();
    if (!bar) return;
    if (scope !== bar.dataset.solScope) {
      scope = bar.dataset.solScope;
      store = window.dwTableSelection.createStore(storage(), {
        key: 'operaciones-solicitudes-selection:' + scope, idPattern: /^[1-9][0-9]*$/, max: Number.MAX_SAFE_INTEGER,
      });
    }
    const available = bar.dataset.solSourceOk === 'yes';
    if (available) store.reconcile(JSON.parse(bar.dataset.solUniverse));
    let visibleSelected = 0;
    document.querySelectorAll('.solicitudes-table').forEach(table => {
      visibleSelected += window.dwTableSelection.syncRows(table, store, {
        row: '[data-sol-select]', page: '[data-sol-select-page]', attribute: 'data-sol-select',
      }).visibleSelected;
    });
    bar.hidden = store.size() === 0;
    bar.querySelector('[data-sol-selection-count]').textContent = store.size();
    bar.querySelector('[data-sol-selection-label]').textContent = store.size() === 1 ? 'solicitud seleccionada' : 'solicitudes seleccionadas';
    const outside = store.size() - visibleSelected;
    bar.querySelector('[data-sol-selection-hint]').textContent = !available ? 'Actualizá la consulta para validar la selección.' : outside > 0 ? outside + ' fuera de esta página o filtro' : '';
    bar.querySelectorAll('[data-sol-action]').forEach(button => {
      button.disabled = !available || !store.size() || (['consolidar', 'preparar'].includes(button.dataset.solAction) && store.size() < 2);
    });
  }
  document.addEventListener('change', event => {
    if (!store) return;
    const box = event.target;
    if (box.matches('[data-sol-select], [data-sol-select-page]')) {
      const ids = box.matches('[data-sol-select]') ? [box.dataset.solSelect] : Array.from(box.closest('table').querySelectorAll('[data-sol-select]'), b => b.dataset.solSelect);
      window.dwTableSelection.keepInPlace(document, box.closest('tr'), () => {
        if (box.checked) store.add(ids); else store.remove(ids);
        sync();
      });
    }
  });
  document.addEventListener('htmx:configRequest', event => {
    if (event.detail?.elt?.matches('[data-sol-action]') && store) {
      event.detail.parameters.selected_ids = JSON.stringify(store.ids());
    }
  });
  document.addEventListener('htmx:beforeRequest', event => {
    if (!event.detail?.elt?.matches('[data-sol-action]')) return;
    const dialog = document.getElementById('solicitudes-bulk-dialog');
    const content = document.getElementById('solicitudes-bulk-content');
    const body = document.createElement('div'); body.className = 'dw-confirm__body';
    const title = document.createElement('h2'); title.className = 'dw-confirm__title'; title.id = 'solicitudes-bulk-title'; title.textContent = 'Consultando solicitudes…';
    body.appendChild(title); content.replaceChildren(body);
    if (!dialog.open) dialog.showModal();
  });
  function requestError(event) {
    if (event.detail?.target?.id !== 'solicitudes-bulk-content') return;
    const content = document.getElementById('solicitudes-bulk-content');
    const text = document.createElement('p');
    const form = event.detail?.elt?.closest('form');
    const planId = form?.querySelector('[name=plan_id]')?.value;
    text.className = 'callout callout--feedback callout--' + (planId ? 'punch' : 'warn');
    text.setAttribute('role', 'alert');
    text.textContent = planId ? 'No se pudo confirmar la respuesta. No reenvíes el lote; consultá sus resultados.' : 'No se pudo completar la consulta. Conservamos la selección; verificá tu sesión y reintentá.';
    const button = document.createElement('button'); button.type = 'button'; button.className = 'btn-secondary';
    button.dataset.solDialogClose = ''; button.textContent = 'Cerrar';
    const body = document.createElement('div'); body.className = 'dw-confirm__body'; body.appendChild(text);
    const actions = document.createElement('div'); actions.className = 'dw-confirm__actions'; actions.appendChild(button);
    if (planId) {
      const recover = document.createElement('button'); recover.type = 'button'; recover.className = 'btn-primary'; recover.textContent = 'Consultar resultados';
      recover.setAttribute('hx-post', '/operaciones/solicitudes/bulk/estado');
      recover.setAttribute('hx-target', '#solicitudes-bulk-content');
      recover.setAttribute('hx-vals', JSON.stringify({plan_id: planId}));
      recover.setAttribute('data-dw-graph-token', ''); actions.prepend(recover);
    }
    content.replaceChildren(body, actions);
    if (planId) htmx.process(actions);
  }
  document.addEventListener('htmx:responseError', requestError);
  document.addEventListener('htmx:sendError', requestError);
  document.addEventListener('htmx:afterSwap', event => {
    if (event.detail?.target?.id === 'solicitudes-panel') sync();
  });
  document.addEventListener('click', async event => {
    if (event.target.closest('[data-sol-clear-selection]') && store) { store.clear(); sync(); return; }
    const dialog = document.getElementById('solicitudes-bulk-dialog');
    if (event.target.closest('[data-sol-dialog-close]')) { dialog?.close(); return; }
    if (event.target.closest('[data-sol-refresh-results]')) { dialog?.close(); document.getElementById('solicitudes-refresh')?.click(); return; }
    const button = event.target.closest('[data-sol-copy]');
    if (!button) return;
    const links = Array.from(dialog.querySelectorAll('[data-sol-share-link]'), link => ({
      title: link.textContent, url: new URL(link.getAttribute('href'), window.location.origin).href,
    }));
    const text = button.dataset.solCopy === 'links' ? links.map(link => link.url).join('\n') :
      dialog.querySelector('#sol-share-message').value + '\n\n' + links.map(link => '- ' + link.title + ': ' + link.url).join('\n');
    let copied = false;
    try { copied = await copyTextToClipboard(text); } catch (_) { /* feedback below */ }
    const status = dialog.querySelector('[data-sol-copy-status]');
    status.className = 'callout callout--feedback callout--' + (copied ? 'success' : 'warn');
    status.textContent = copied ? 'Copiado al portapapeles.' : 'No se pudo copiar. Seleccioná los enlaces y copialos manualmente.';
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync); else sync();
})();

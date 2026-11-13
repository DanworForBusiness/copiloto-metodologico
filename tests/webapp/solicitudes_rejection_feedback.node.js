const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname, '../../webapp/static/solicitudes_write.js'), 'utf8');

function environment() {
  const events = {};
  const node = () => ({children: [], textContent: '', setAttribute(){},
    appendChild(n){this.children.push(n); return n;}, replaceChildren(...nodes){this.children = nodes;}});
  const feedback = node();
  const reason = {value: 'Justificación que no debe perderse', readOnly:false};
  let closed = 0, requests = 0;
  const form = {
    matches: () => true,
    closest: selector => selector.includes('data-solicitudes-admission') ? null : form,
    getAttribute: () => '/operaciones/solicitudes/1/rechazar',
    querySelector: selector => selector === '[name=reason]' ? reason : feedback,
    querySelectorAll: () => [reason],
  };
  const dialog = {close(){closed++;}, querySelector: form.querySelector};
  const window = {dwSolicitudesWriteToken: async () => 'fake', matchMedia: () => ({matches:true})};
  vm.runInNewContext(source, {window, document:{
    addEventListener: (name, fn) => {events[name] = fn;}, createElement:node,
    getElementById: () => dialog, querySelectorAll: () => [],
  }});
  const detail = {elt:form, xhr:{status:403}, issueRequest(){requests++;}};
  return {events, form, reason, feedback, window, detail, closed:()=>closed, requests:()=>requests};
}

(async () => {
  for (const event of ['htmx:responseError','htmx:sendError','htmx:timeout','htmx:sendAbort']) {
    const env = environment();
    env.events['htmx:beforeRequest']({detail:env.detail});
    assert.equal(env.reason.readOnly, true);
    env.events[event]({detail:env.detail});
    env.events['htmx:afterRequest']({detail:env.detail});
    assert.equal(env.reason.readOnly, false);
    assert.equal(env.reason.value, 'Justificación que no debe perderse');
    assert.equal(env.closed(), 0);
    assert.equal(env.requests(), 0, 'Failure must not retry the write');
    assert.match(env.feedback.children[0].children[0].textContent,
      event === 'htmx:responseError' ? /No tenés permiso/ : /no pudimos confirmar/);
    assert.equal(env.feedback.children[0].children[1].href, '/operaciones/solicitudes/1/editar');
  }
  const auth = environment();
  auth.window.dwSolicitudesWriteToken = async () => {throw Error('PRIVATE');};
  await auth.events['htmx:confirm']({detail:auth.detail, preventDefault(){}});
  assert.equal(auth.requests(), 0);
  assert.match(auth.feedback.children[0].children[0].textContent, /No se pudo iniciar/);
  assert.doesNotMatch(auth.feedback.children[0].children[0].textContent, /PRIVATE/);
  assert.equal(auth.closed(), 0);

  const malformed = environment();
  malformed.events['htmx:afterSwap']({detail:{requestConfig:{elt:malformed.form},
    target:{id:'solicitudes-save-result', querySelector:()=>null}}});
  assert.match(malformed.feedback.children[0].children[0].textContent, /respuesta no permitió verificar/);
  assert.equal(malformed.closed(), 0);
  assert.equal(malformed.reason.value, 'Justificación que no debe perderse');

  for (const saved of [false, true]) {
    const env = environment();
    env.events['htmx:afterSwap']({detail:{requestConfig:{elt:env.form}, target:{
      id:'solicitudes-save-result', innerHTML:'rendered feedback',
      querySelector: selector => selector === '[data-solicitudes-rejection]' || saved ? {} : null,
    }}});
    assert.equal(env.closed(), saved ? 1 : 0);
    assert.equal(env.reason.value, saved ? '' : 'Justificación que no debe perderse');
    if (!saved) assert.equal(env.feedback.innerHTML, 'rendered feedback');
  }
  console.log('Rejection HTTP/network/auth/malformed response, draft retention, success and no-retry: OK');
})().catch(error => {console.error(error); process.exitCode=1;});

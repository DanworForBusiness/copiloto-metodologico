const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname, '../../webapp/static/solicitudes_write.js'), 'utf8');
async function check(status, planned, actual, funnel, accepted, expectedPrompt) {
  const events = {};
  let prompts = 0, requests = 0, tokens = 0;
  const form = {
    dataset: {solicitudesAdmission:funnel},
    closest: () => form,
    querySelector: id => ({'#sol-status': {value:status}, '#sol-planned':{value:planned,defaultValue:planned}, '#sol-actual':{value:actual,defaultValue:actual}}[id]),
  };
  vm.runInNewContext(source, {
    document: {addEventListener:(name,fn)=>{events[name]=fn;}},
    window: {dwSolicitudesWriteToken:async()=>{tokens++;return 'fixture';}},
    dwConfirm:async()=>{prompts++;return accepted;},
  });
  await events['htmx:confirm']({preventDefault(){},detail:{elt:form,issueRequest(){requests++;}}});
  assert.equal(prompts,expectedPrompt?1:0);
  const proceed = !expectedPrompt || accepted;
  assert.equal(requests,proceed?1:0);
  assert.equal(tokens,proceed?1:0,'Cancel must not acquire a writer or send');
  if(proceed){
    const detail={elt:form,parameters:{}};
    events['htmx:configRequest']({detail});
    assert.equal(detail.parameters.confirm_admission,expectedPrompt?'yes':undefined);
  }
}
(async()=>{
  for(const state of ['Planificado','En curso','Finalizado']){
    await check(state,'','','Solicitado',true,true);
    await check(state,'','','Solicitado',false,true);
  }
  await check('Pendiente','2026-11-04','','Solicitado',true,true);
  await check('Pendiente','','2026-11-04','Solicitado',false,true);
  await check('Pendiente','','','Solicitado',true,false);
  for(const funnel of ['Aprobado para Arquitectura','Rechazado, Aplica Operaciones'])
    await check('En curso','2026-11-04','',funnel,true,false);
  console.log('Admission effective-state confirmation and cancellation: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

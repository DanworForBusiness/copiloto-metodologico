const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname,'../../webapp/static/app.js'),'utf8');
const code = source.slice(source.indexOf('// CCHH feedback:'),source.indexOf('// ── Rating de estrellas'));
function note(auto=false,controls=false){
 const attrs=new Set(auto?['data-dw-auto-dismiss']:[]),classes=new Set();
 return {textContent:'Mensaje',removed:false,close:null,
  classList:{add:k=>classes.add(k),remove:k=>classes.delete(k)},
  setAttribute:k=>attrs.add(k),matches:()=>auto,
  querySelector:s=>s.startsWith('input')?(controls?{}:null):s.startsWith(':scope')?this:null,
  appendChild(button){this.close=button;button.closest=()=>this;},
  prepend(icon){this.icon=icon;},
  remove(){this.removed=true;},replaceChildren(){this.close=null;this.textContent='';},
  controls,
 };
}
function harness(url){
 const notices=[],events={};let observer;
 const document={readyState:'complete',body:{},
  querySelectorAll:()=>notices.filter(n=>!n.removed),
  createElement:()=>({setAttribute(){},closest(){return this;}}),
  addEventListener:(name,fn)=>{events[name]=fn;},
 };
 function add(auto=false,controls=false){
  const n=note(auto,controls);
  n.querySelector=s=>s.startsWith('input')?(controls?{}:null):s.startsWith(':scope')?n.close:s.startsWith('.callout__icon')?n.icon:null;
  n.matches=s=>s==='[data-dw-auto-dismiss]'?auto:false;
  notices.push(n);return n;
 }
 const initial=add(),toast=add(true),form=add(false,true);
 vm.runInNewContext(code,{document,window:{location:{pathname:url}},queueMicrotask:fn=>fn(),
  MutationObserver:class{constructor(fn){observer=fn;}observe(){}},
  setTimeout(){throw Error('Dismiss must preserve existing timers');},clearTimeout(){throw Error('Dismiss must preserve existing timers');},
 });
 return {initial,toast,form,add,events,refresh:()=>observer?.()};
}
const h=harness('/cchh/candidatos/importar');
assert.ok(h.initial.close);assert.ok(h.toast.close);assert.equal(h.form.close,null);
const button=h.initial.close;h.refresh();assert.equal(h.initial.close,button,'No duplicate close buttons');
const fresh=h.add();h.refresh();assert.ok(fresh.close,'Dynamic HTMX feedback is closable');
function click(n){h.events.click({target:{closest:()=>n.close},preventDefault(){},stopPropagation(){}});}
click(h.initial);assert.ok(h.initial.removed);
click(h.toast);assert.equal(h.toast.textContent,'');assert.equal(h.toast.removed,false,'Keep the auto-dismiss host for future messages');
const operations=harness('/operaciones/solicitudes');
assert.ok(operations.initial.close);assert.ok(operations.initial.icon);
const opsIcon=operations.initial.icon;operations.refresh();assert.equal(operations.initial.icon,opsIcon,'No duplicate feedback icons');
assert.equal(operations.form.close,null,'Forms remain intact in Operations');
const dynamic=operations.add();operations.refresh();assert.ok(dynamic.close);assert.ok(dynamic.icon);
const other=harness('/comercial/crm');assert.equal(other.initial.close,null);
console.log('CCHH notices: dynamic close, no duplicates, preserved timers/hosts and vertical isolation OK');

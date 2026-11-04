const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const events = {};
let scrolled = [], reduced = false;
const feedback = {
  getBoundingClientRect: () => ({top: 900, bottom: 970}),
  scrollIntoView: options => scrolled.push(options),
};
const target = {id: 'solicitudes-save-result', querySelector: () => feedback};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../webapp/static/solicitudes_write.js'), 'utf8'), {
  document: {
    addEventListener: (name, fn) => { events[name] = fn; },
    querySelector: () => ({getBoundingClientRect: () => ({bottom: 70})}),
  },
  window: {innerHeight: 800, matchMedia: () => ({matches: reduced})},
});
const settle = value => events['htmx:afterSettle']({detail: {target: value || target}});
settle();
assert.equal(scrolled.length, 1);
assert.equal(scrolled[0].behavior, 'smooth');
assert.equal(scrolled[0].block, 'center');
scrolled = [];
feedback.getBoundingClientRect = () => ({top: 100, bottom: 170});
settle();
assert.equal(scrolled.length, 0, 'Already visible: no movement');
feedback.getBoundingClientRect = () => ({top: 20, bottom: 90});
reduced = true;
settle();
assert.equal(scrolled[0].behavior, 'instant');
scrolled = [];
settle({id: 'other-panel', querySelector: () => feedback});
settle({id: 'solicitudes-save-result', querySelector: () => null});
assert.equal(scrolled.length, 0, 'Unrelated swaps and errors do not scroll');
console.log('Feedback visibility: OK');

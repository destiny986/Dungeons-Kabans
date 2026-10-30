const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Exercise the real delegated click handler with exclusive radio groups.
const listeners = {};
const document = {
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (name, listener) => (listeners[name] ||= []).push(listener),
};
vm.runInNewContext(fs.readFileSync('game/static/game/app.js', 'utf8'), {document});
function group(values) {
  const inputs = values.map(value => ({value, _checked:false}));
  inputs.forEach(input => Object.defineProperty(input, 'checked', {
    get() { return this._checked; },
    set(value) { if (value) inputs.forEach(other => { other._checked = false; }); this._checked = value; },
  }));
  inputs[0].checked = true;
  return {inputs, querySelectorAll: () => inputs};
}
const groups = [group(['existing','incoming_1','incoming_2']), group(['incoming_0','incoming_1'])];
const form = {querySelectorAll: () => groups};
function click(mode) {
  const bulk = {dataset:{importBulk:mode}, closest: () => form};
  const target = {tagName:'BUTTON', closest: selector => selector === '[data-import-bulk]' ? bulk : null};
  listeners.click.forEach(listener => listener({target}));
}
const selected = () => groups.map(group => group.inputs.find(input => input.checked).value);
click('incoming');
assert.deepEqual(selected(), ['incoming_1','incoming_0']);
groups[0].inputs[2].checked = true;
assert.deepEqual(selected(), ['incoming_2','incoming_0']); // Individual override.
click('keep');
assert.deepEqual(selected(), ['existing','incoming_0']);
console.log('Import bulk choices: passed (existing, absent existing, multiple incoming, individual override).');

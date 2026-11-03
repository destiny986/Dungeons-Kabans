const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Run the real delegated input/change handlers against a minimal editor DOM.
const listeners = {};
const fields = {
  activation: {value:'reaction', selectedOptions:[{textContent:'Реакция'}]},
  resource: {value:'spell_slot', selectedOptions:[{textContent:'Ячейка заклинания'}]},
  slot_level: {value:'7'}, description: {value:'Первая строка\n<Вторая строка>'},
};
const activation = {dataset:{previewSelect:'activation'}, textContent:''};
const resource = {dataset:{previewSelect:'resource'}, textContent:''};
const level = {dataset:{preview:'slot_level'}, textContent:''};
const description = {dataset:{preview:'description'}, textContent:''};
const slot = {hidden:true};
const image = {dataset:{}, hidden:true};
const activationBadge = {hidden:false}, resourceBadge = {hidden:false};
const symbol = {textContent:''};
const metadata = {
  hidden:false, dataset:{activationSymbols:JSON.stringify({action:'◯',bonus:'△',reaction:'✧',free:''})},
  querySelector: selector => ({'[data-action-badge="activation"]':activationBadge,
    '[data-action-badge="resource"]':resourceBadge, '[data-preview-activation-symbol]':symbol}[selector]),
};
const metadataText = () => [activationBadge.hidden ? '' : `${symbol.textContent} ${activation.textContent}`, resourceBadge.hidden ? '' : `▣ ${resource.textContent}${slot.hidden ? '' : ` · ${level.textContent} уровень`}`].filter(Boolean).join(' ');
const root = {
  querySelector: () => null,
  querySelectorAll: selector => ({
    '[data-preview]':[level, description], '[data-preview-select]':[activation, resource],
    '[data-action-meta][data-activation-symbols]':[metadata], '[data-preview-slot]':[slot], '[data-preview-image]':[image],
  }[selector] || []),
};
const form = {
  elements:{namedItem:name => fields[name]},
  closest: selector => selector === '.editor-layout' ? root : null,
};
const document = {
  querySelector: selector => selector === '[name=resource]' ? fields.resource : null,
  querySelectorAll: () => [],
  addEventListener: (name, listener) => (listeners[name] ||= []).push(listener),
};
const revoked = [];
let objectId = 0;
const URL = {createObjectURL: () => `blob:${++objectId}`, revokeObjectURL: value => revoked.push(value)};
vm.runInNewContext(fs.readFileSync('game/static/game/app.js', 'utf8'), {document, URL});
function dispatch(name, target = {}) {
  target.matches = () => false;
  target.closest = selector => selector === '[data-preview-form]' ? form : null;
  listeners[name].forEach(listener => listener({target}));
}
dispatch('input');
assert.equal(metadataText(), '✧ Реакция ▣ Ячейка заклинания · 7 уровень');
assert.equal(description.textContent, 'Первая строка\n<Вторая строка>');
for (const [value, label] of [['none','Нет'], ['rage','Ярость'], ['wildshape','Дикий облик']]) {
  fields.resource = {value, selectedOptions:[{textContent:label}]};
  dispatch('change');
  assert.equal(metadataText(), value === 'none' ? '✧ Реакция' : `✧ Реакция ▣ ${label}`);
}
fields.resource = {value:'spell_slot', selectedOptions:[{textContent:'Ячейка заклинания'}]};
fields.activation = {value:'bonus', selectedOptions:[{textContent:'Бонусное действие'}]};
fields.slot_level.value = '3';
dispatch('input');
assert.equal(metadataText(), '△ Бонусное действие ▣ Ячейка заклинания · 3 уровень');
fields.activation = {value:'free', selectedOptions:[{textContent:'Бесплатно'}]};
fields.resource = {value:'none', selectedOptions:[{textContent:'Нет'}]};
dispatch('change');
assert.equal(metadata.hidden, true);
assert.equal(metadataText(), '');
fields.resource = {value:'rage', selectedOptions:[{textContent:'Ярость'}]};
dispatch('change');
assert.equal(metadata.hidden, false);
assert.equal(metadataText(), '▣ Ярость');

dispatch('change', {type:'file', name:'icon', files:[{}]});
assert.equal(image.src, 'blob:1');
assert.equal(image.hidden, false);
dispatch('change', {type:'file', name:'icon', files:[{}]});
assert.equal(image.src, 'blob:2');
assert.deepEqual(revoked, ['blob:1']);
console.log('Catalog preview checks passed');

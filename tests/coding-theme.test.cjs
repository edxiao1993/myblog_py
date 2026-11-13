const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup(stored, dark = false) {
  const handlers = {}, toggle = {checked: false, addEventListener: (name, fn) => handlers[name] = fn};
  const media = {matches: dark, addEventListener: (name, fn) => handlers.system = fn};
  const document = {body: {dataset: {}}, getElementById: () => toggle};
  const localStorage = {getItem: () => stored, setItem: (key, value) => { stored = value; }};
  vm.runInNewContext(readFileSync('static/js/coding-theme.js', 'utf8'), {document, localStorage, window: {matchMedia: () => media}});
  return {document, toggle, handlers, stored: () => stored};
}

test('follows system preference until the user chooses and persists the choice', () => {
  const ui = setup(null, true);
  assert.equal(ui.document.body.dataset.codingTheme, 'dark');
  ui.handlers.system({matches: false});
  assert.equal(ui.toggle.checked, false);
  ui.toggle.checked = true;
  ui.handlers.change();
  assert.equal(ui.stored(), 'dark');
  ui.handlers.system({matches: false});
  assert.equal(ui.document.body.dataset.codingTheme, 'dark');
  assert.equal(setup(ui.stored(), false).toggle.checked, true);
  assert.equal(setup('light', true).toggle.checked, false);
});

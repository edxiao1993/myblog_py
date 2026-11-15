const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
function setup(stored, dark = false, legacy = null, unavailable = false) {
  const handlers = {}, preferences = new Map([['app-theme',stored],['coding-agent-theme',legacy]]);
  const toggles = [0,1].map(() => ({checked:false, handlers:{}, addEventListener(name,fn){this.handlers[name]=fn;}}));
  const document = {readyState:'loading',documentElement:{dataset:{}}, querySelectorAll:() => toggles, addEventListener:(name,fn) => {handlers[name]=fn;}};
  const localStorage = {getItem:key => {if(unavailable)throw Error('blocked');return preferences.get(key);},setItem:(key,value) => {if(unavailable)throw Error('blocked');preferences.set(key,value);}};
  vm.runInNewContext(readFileSync('static/js/theme.js','utf8'), {document,localStorage,window:{matchMedia:() => ({matches:dark,addEventListener:(name,fn) => {handlers.system=fn;}})}});
  return {document,toggles,handlers,preferences};
}
test('applies theme before body renders, syncs switches, persists and follows system until chosen', () => {
  const ui = setup(null,true);
  assert.equal(ui.document.documentElement.dataset.theme,'dark');
  assert.equal(ui.document.documentElement.dataset.bsTheme,'dark');
  ui.handlers.DOMContentLoaded();
  assert.equal(ui.toggles[0].checked,true);
  ui.handlers.system({matches:false});
  assert.equal(ui.toggles[1].checked,false);
  ui.toggles[0].checked=true; ui.toggles[0].handlers.change();
  assert.equal(ui.toggles[1].checked,true);
  assert.equal(ui.preferences.get('app-theme'),'dark');
  ui.handlers.system({matches:false});
  assert.equal(ui.document.documentElement.dataset.theme,'dark');
  assert.equal(setup('light',true).document.documentElement.dataset.theme,'light');
});
test('keeps the old Coding Agent preference and works when storage is blocked', () => {
  assert.equal(setup(null,false,'dark').document.documentElement.dataset.theme,'dark');
  const ui=setup(null,true,null,true); ui.handlers.DOMContentLoaded();
  ui.toggles[0].checked=false; ui.toggles[0].handlers.change();
  assert.equal(ui.document.documentElement.dataset.theme,'light');
});

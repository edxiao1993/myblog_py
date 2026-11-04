// Run with: node --test tests/pi-agent-ui.test.cjs (no npm dependencies).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

// Only the DOM operations used by the activity renderer are needed here.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.textContent = '';
    const classes = new Set();
    this.classList = {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c)};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener() {}
}
function setup() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  get('pi-presets').textContent = '{}';
  const context = {window: {}, document: {getElementById: get, createElement: tag => new Element(tag)},
    localStorage: {setItem() {}}, console};
  vm.createContext(context);
  const source = readFileSync('static/js/pi-agent.js', 'utf8').replace('  initialize();',
    '  window.testUI = {event, assistantDetails, resetMessages, setToolsVisibility};');
  vm.runInContext(source, context);
  return {ui: context.window.testUI, get};
}

test('thinking streams, finalizes, restores, and stays collapsed across updates', () => {
  const {ui, get} = setup();
  ui.event({type: 'message_start', message: {role: 'assistant'}});
  const update = (type, extra) => ui.event({type: 'message_update', assistantMessageEvent: {type, contentIndex: 0, ...extra}});
  update('thinking_delta', {delta: 'First '});
  update('thinking_delta', {delta: '<script>thought</script>'});
  const card = get('pi-messages').children[0].children[0];
  assert.equal(card.tagName, 'details');
  assert.equal(card.children[0].tagName, 'summary');
  assert.equal(card.open, false);
  const body = card.children[1].children[0];
  assert.equal(body.textContent, 'First <script>thought</script>');
  card.open = false;
  update('thinking_end', {content: 'Final thought'});
  ui.event({type: 'message_end', message: {role: 'assistant', content: [{type: 'thinking', thinking: 'Final thought'}]}});
  assert.equal(card.open, false);
  assert.equal(body.textContent, 'Final thought');
  assert.equal(get('pi-messages').children.length, 1);
  ui.event({type: 'message_start', message: {role: 'assistant'}});
  update('thinking_delta', {delta: 'Next turn'});
  assert.equal(get('pi-messages').children.length, 2);
  ui.resetMessages();
  ui.assistantDetails({content: [{type: 'thinking', thinking: 'Saved thought'}]});
  const restored = get('pi-messages').children[0].children[0];
  assert.equal(restored.open, false);
  assert.equal(restored.children[1].children[0].textContent, 'Saved thought');
});

test('tool cards update independently and both visibility switches stay in sync', () => {
  const {ui, get} = setup();
  ui.event({type: 'tool_execution_start', toolCallId: 'a', toolName: '<img onerror=alert(1)>', args: {path: 'x'}});
  const card = get('pi-messages').children[0].children[0];
  assert.ok(card.children[0].textContent.includes('<img onerror=alert(1)>'));
  assert.equal(card.open, false);
  card.open = true;
  ui.event({type: 'tool_execution_end', toolCallId: 'a', result: {content: [{type: 'text', text: 'done'}]}});
  assert.equal(card.open, true);
  assert.equal(card.children[1].children[0].textContent, 'done');
  ui.event({type: 'tool_execution_start', toolCallId: 'b', toolName: 'read', args: {}});
  assert.equal(get('pi-messages').children.length, 2);
  const read = get('pi-messages').children[1].children[0];
  assert.equal(read.open, false);
  assert.equal(read.children[0].textContent, '⚙️ read');
  for (const show of [true, false, true]) {
    get('pi-settings-toggle-tools').onchange({target: {checked: show}});
    assert.equal(get('pi-app').classList.contains('hide-tools'), !show);
    assert.equal(get('pi-toggle-tools').checked, show);
    assert.equal(get('pi-settings-toggle-tools').checked, show);
  }
});

// Run with: node --test tests/pi-agent-ui.test.cjs (no npm dependencies).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

// Minimal DOM operations and scroll metrics for the streaming UI.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.textContent = '';
    this.listeners = {}; this.scrollHeight = 0; this.clientHeight = 0; this._scrollTop = 0;
    const classes = new Set();
    this.classList = {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c), toggle: (c, show) => show ? classes.add(c) : classes.delete(c)};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatch(type, values = {}) { for (const fn of this.listeners[type] || []) fn(values); }
  querySelectorAll() { return []; }
  setAttribute(name, value) { this[name] = value; }
  get scrollTop() { return this._scrollTop; }
  set scrollTop(value) { this._scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); }
}
function setup() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  get('pi-presets').textContent = '{}';
  const context = {window: new Element('window'), document: {getElementById: get, createElement: tag => new Element(tag), querySelectorAll: () => []},
    marked: {setOptions() {}, parse: text => text}, DOMPurify: {addHook() {}, sanitize: text => text},
    localStorage: {setItem() {}}, console};
  vm.createContext(context);
  const source = readFileSync('static/js/pi-agent.js', 'utf8').replace('  initialize();',
    '  window.testUI = {event, assistantDetails, resetMessages, setToolsVisibility, setBusy};');
  vm.runInContext(source, context);
  context.window.testUI.setToolsVisibility(false);
  return {ui: context.window.testUI, get, window: context.window};
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


test('empty greeting disappears with an activity and returns for a new chat', () => {
  const {ui, get} = setup();
  ui.resetMessages();
  assert.equal(get('pi-empty-state').hidden, false);
  ui.event({type: 'tool_execution_start', toolCallId: 'a', toolName: 'read', args: {}});
  assert.equal(get('pi-empty-state').hidden, true);
  ui.resetMessages();
  assert.equal(get('pi-empty-state').hidden, false);
});


const delta = text => ({type: 'message_update', assistantMessageEvent: {type: 'text_delta', delta: text}});

test('hidden activity tip reflects thinking and tools, toggles, then clears on answers and completion', () => {
  const {ui, get} = setup();
  const tip = get('pi-activity-tip'), label = get('pi-activity-text');
  ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_start'}});
  assert.equal(tip.hidden, false);
  assert.equal(label.textContent, 'Thinking…');
  ui.setToolsVisibility(true);
  assert.equal(tip.hidden, true);
  ui.setToolsVisibility(false);
  assert.equal(tip.hidden, false);
  ui.event({type: 'tool_execution_start', toolCallId: 'a', toolName: '<read>', args: {}});
  assert.equal(label.textContent, 'Running <read>…');
  ui.event({type: 'tool_execution_end', toolCallId: 'a', toolName: '<read>', result: {content: []}});
  assert.equal(label.textContent, 'Preparing a response…');
  ui.event(delta('Here is the answer.'));
  assert.equal(tip.hidden, true);
  ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_delta', contentIndex: 0, delta: 'more'}});
  assert.equal(tip.hidden, false);
  ui.event({type: 'done'});
  assert.equal(tip.hidden, true);
  ui.event({type: 'tool_execution_start', toolCallId: 'b', toolName: 'read', args: {}});
  assert.throws(() => ui.event({type: 'error', message: 'Stopped'}));
  assert.equal(tip.hidden, true);
  ui.event({type: 'tool_execution_start', toolCallId: 'c', toolName: 'read', args: {}});
  ui.setBusy(false); // Includes disconnect and cancellation cleanup.
  assert.equal(tip.hidden, true);
});

test('touch pauses scroll while text keeps streaming; latest button resumes following', () => {
  const {ui, get, window} = setup();
  const messages = get('pi-messages');
  messages.clientHeight = 200;
  messages.scrollHeight = 1000;
  ui.event(delta('First '));
  assert.equal(messages.scrollTop, 800);
  messages.dispatch('pointerdown');
  messages.scrollHeight = 1400;
  ui.event(delta('second '));
  assert.equal(messages.scrollTop, 800);
  assert.equal(get('pi-jump-latest').hidden, false);
  window.dispatch('pointerup');
  messages.scrollHeight = 1600;
  ui.event(delta('third'));
  assert.equal(messages.scrollTop, 800);
  const body = messages.children[0].children[0].children[1].children[0];
  assert.equal(body.dataset.text, 'First second third');
  get('pi-jump-latest').onclick();
  assert.equal(messages.scrollTop, 1400);
  assert.equal(get('pi-jump-latest').hidden, true);
  messages.scrollHeight = 1800;
  ui.event(delta(' fourth'));
  assert.equal(messages.scrollTop, 1600);
});

test('scrolling back to bottom resumes; final answers respect a paused reader', () => {
  const {ui, get} = setup();
  const messages = get('pi-messages');
  messages.clientHeight = 200; messages.scrollHeight = 1000;
  ui.event(delta('Start'));
  messages.dispatch('wheel');
  messages.scrollTop = 300; messages.dispatch('scroll');
  messages.scrollHeight = 1400;
  ui.event({type: 'message_end', message: {role: 'assistant', content: [{type: 'text', text: 'Final answer'}]}});
  assert.equal(messages.scrollTop, 300);
  messages.scrollTop = 1200; messages.dispatch('scroll');
  messages.scrollHeight = 1600;
  ui.event(delta('Next answer'));
  assert.equal(messages.scrollTop, 1400);
  messages.dispatch('pointerdown');
  ui.resetMessages();
  messages.scrollHeight = 1800;
  ui.event(delta('New conversation'));
  assert.equal(messages.scrollTop, 1600);
});


test('parallel tool completion keeps naming the remaining work', () => {
  const {ui, get} = setup();
  ui.event({type: 'tool_execution_start', toolCallId: 'a', toolName: 'read', args: {}});
  ui.event({type: 'tool_execution_start', toolCallId: 'b', toolName: 'bash', args: {}});
  ui.event({type: 'tool_execution_end', toolCallId: 'a', result: {content: []}});
  assert.equal(get('pi-activity-text').textContent, 'Running bash…');
  ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_end', contentIndex: 0, content: 'done'}});
  assert.equal(get('pi-activity-text').textContent, 'Running bash…');
  ui.event({type: 'tool_execution_end', toolCallId: 'b', result: {content: []}});
  assert.equal(get('pi-activity-text').textContent, 'Preparing a response…');
  ui.event({type: 'message_end', message: {role: 'assistant', content: 'Finished.'}});
  assert.equal(get('pi-activity-tip').hidden, true);
});

test('native touch pointer cancellation does not release the reading pause', () => {
  const {ui, get, window} = setup();
  const messages = get('pi-messages');
  messages.clientHeight = 200; messages.scrollHeight = 1000;
  ui.event(delta('Start'));
  messages.dispatch('pointerdown'); messages.dispatch('touchstart');
  window.dispatch('pointercancel');
  messages.scrollTop = 500; messages.dispatch('scroll');
  messages.scrollTop = 800; messages.dispatch('scroll');
  messages.scrollHeight = 1400;
  ui.event(delta('More'));
  assert.equal(messages.scrollTop, 800);
  window.dispatch('touchend', {touches: []});
  messages.scrollTop = 1200; messages.dispatch('scroll');
  messages.scrollHeight = 1600;
  ui.event(delta('Latest'));
  assert.equal(messages.scrollTop, 1400);
});

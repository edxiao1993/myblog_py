// Run with: node --test tests/pi-agent-ui.test.cjs (no npm dependencies).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

// Minimal DOM operations and scroll metrics for the streaming UI.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.textContent = '';
    this.style = {removeProperty(name) { delete this[name]; }}; this.value = '';
    this.listeners = {}; this.scrollHeight = 0; this.clientHeight = 0; this._scrollTop = 0;
    this.parentElement = null;
    const classes = new Set();
    this.classList = {
      add: (...cs) => cs.forEach(c => classes.add(c)),
      remove: (...cs) => cs.forEach(c => classes.delete(c)),
      contains: c => classes.has(c),
      toggle: (c, show) => show ? classes.add(c) : classes.delete(c)
    };
    let _className = '';
    this.options = [];
    Object.defineProperty(this, 'className', {
      get: () => _className,
      set: val => {
        _className = String(val || '');
        classes.clear();
        _className.split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
      }
    });
  }
  click() { this.onclick?.({preventDefault() {}}); }
  add(opt) { this.options.push(opt); }
  focus() {}
  showModal() { this.open = true; }
  close(value) { this.returnValue = value; this.open = false; this.onclose?.(); }
  append(...children) {
    for (const child of children) {
      if (child instanceof Element) child.parentElement = this;
    }
    this.children.push(...children);
  }
  replaceChildren(...children) {
    for (const child of children) {
      if (child instanceof Element) child.parentElement = this;
    }
    this.children = children;
  }
  remove() {
    if (this.parentElement) {
      this.parentElement.children = this.parentElement.children.filter(c => c !== this);
    }
  }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatch(type, values = {}) { for (const fn of this.listeners[type] || []) fn(values); }
  setAttribute(name, value) { this[name] = value; }
  getAttribute(name) { return this[name] !== undefined ? this[name] : null; }
  removeAttribute(name) { delete this[name]; }
  closest(selector) {
    let cur = this;
    const className = selector.startsWith('.') ? selector.slice(1) : selector;
    while (cur) {
      if (cur.classList?.contains(className) || cur.tagName?.toLowerCase() === selector.toLowerCase()) return cur;
      cur = cur.parentElement;
    }
    return null;
  }
  querySelector(selector) {
    const list = this.querySelectorAll(selector);
    return list.length ? list[0] : null;
  }
  querySelectorAll(selector = '*') {
    const results = [];
    const match = el => {
      if (!selector || selector === '*') return true;
      if (selector.startsWith('.')) return el.classList?.contains(selector.slice(1));
      return el.tagName?.toLowerCase() === selector.toLowerCase();
    };
    const walk = node => {
      for (const child of node.children || []) {
        if (child instanceof Element) {
          if (match(child)) results.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return results;
  }
  get scrollTop() { return this._scrollTop; }
  set scrollTop(value) { this._scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); }
}
function setup(fetch) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  get('pi-presets').textContent = '{}';
  class MockURL extends URL {
    static createObjectURL() { return 'blob:mock'; }
    static revokeObjectURL() {}
  }
  const context = {Date, fetch, FormData, URL: MockURL, Option: class { constructor(text, val) { this.text = text; this.value = val; } }, setTimeout, clearTimeout, setInterval, clearInterval, location:{href:'https://example.test/pi-agent'}, history:{replaceState(_state,_title,url){this.url=String(url);}}, window: new Element('window'), document: {body:new Element('body'), getElementById: get, createElement: tag => new Element(tag), querySelectorAll: () => []},
    marked: {setOptions() {}, parse: text => text}, DOMPurify: {addHook() {}, sanitize: text => text},
    localStorage: {setItem() {}}, console};
  context.window.innerWidth = 1024; context.window.innerHeight = 800;
  context.window.visualViewport = new Element('viewport');
  Object.assign(context.window.visualViewport,{height:800,offsetTop:0,scale:1});
  vm.createContext(context);
  const source = readFileSync('static/js/pi-agent.js', 'utf8').replace('  initialize();',
    '  window.testUI = {event, assistantDetails, resetMessages, setToolsVisibility, setBusy, updateActivityDisplay, refreshHistory, fitMobileViewport, deleteConversation, bubble, render, openConversation, refreshFiles, setCurrent: id => { sid = id; started = true; }, current: () => sid};');
  vm.runInContext(source, context);
  context.window.testUI.setToolsVisibility(false);
  return {ui: context.window.testUI, get, window: context.window, document:context.document, history:context.history};
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

test('mobile viewport keeps the page frame aligned above the keyboard and restores desktop sizing', () => {
  const {ui,get,window,document} = setup();
  window.innerWidth = 390;
  ui.fitMobileViewport();
  assert.equal(document.body.style.height,'800px');
  window.visualViewport.height = 390; window.visualViewport.offsetTop = 170;
  window.visualViewport.dispatch('resize');
  assert.equal(get('pi-app').style.height,'390px');
  assert.equal(document.body.style.height,'390px');
  assert.equal(document.body.style.top,'170px');
  window.visualViewport.offsetTop = 200; window.visualViewport.dispatch('scroll');
  assert.equal(document.body.style.top,'200px');
  window.visualViewport.height = 800; window.visualViewport.offsetTop = 0; window.visualViewport.dispatch('resize');
  assert.equal(document.body.style.height,'800px');
  assert.equal(document.body.style.top,'0px');
  window.innerWidth = 1024; window.dispatch('resize');
  assert.equal(get('pi-app').style.height,undefined);
  assert.equal(document.body.style.height,undefined);
  assert.equal(document.body.style.top,undefined);
});

test('history deletion confirms safely, supports cancellation, and resets the current conversation', async () => {
  const posted = [];
  const item = {id:'a'.repeat(32),title:'<img onerror=alert(1)>',created:1};
  const {ui,get,history} = setup(async (url,options) => {
    if(options.method === 'POST') posted.push(url);
    return {ok:true,json:async () => options.method === 'POST' ? {ok:true} : [item]};
  });
  await ui.refreshHistory();
  const row = get('pi-history').children[0];
  assert.equal(row.children.length,2);
  assert.equal(row.children[0].children[0].textContent,item.title);
  assert.equal(row.children[1]['aria-label'],'Delete conversation: '+item.title);
  let deletion = row.children[1].onclick();
  assert.equal(get('pi-delete-dialog').open,true);
  assert.ok(get('pi-delete-description').textContent.includes(item.title));
  get('pi-delete-cancel').onclick(); await deletion;
  assert.equal(posted.length,0);
  ui.setCurrent(item.id);
  deletion = row.children[1].onclick();
  get('pi-delete-confirm').onclick(); await deletion;
  assert.deepEqual(posted,['/api/pi/sessions/'+item.id+'/delete']);
  assert.equal(ui.current(),'');
  assert.equal(get('pi-messages').children.length,0);
  assert.equal(get('pi-title').textContent,'New conversation');
  assert.equal(get('pi-status').textContent,'Conversation deleted from history.');
  assert.ok(!history.url.includes('conversation='));
});

test('failed or busy history deletion keeps the conversation available', async () => {
  const item = {id:'a'.repeat(32),title:'Keep this chat',created:1};
  const {ui,get} = setup(async (_url,options) => ({ok:options.method !== 'POST',status:409,
    json:async () => options.method === 'POST' ? {error:'This conversation is already running.'} : [item]}));
  await ui.refreshHistory(); ui.setCurrent(item.id);
  ui.setBusy(true);
  await get('pi-history').children[0].children[1].onclick();
  assert.notEqual(get('pi-delete-dialog').open,true);
  ui.setBusy(false);
  const deletion = get('pi-history').children[0].children[1].onclick();
  get('pi-delete-confirm').onclick(); await deletion;
  assert.equal(ui.current(),item.id);
  assert.equal(get('pi-history').children[0].children[0].children[0].textContent,item.title);
  assert.equal(get('pi-status').textContent,'This conversation is already running.');
});

test('keyboard viewport changes preserve a paused reader and do not disable pinch zoom', () => {
  const {ui,get,window,document} = setup();
  const messages = get('pi-messages');
  messages.clientHeight = 200; messages.scrollHeight = 1000;
  ui.event(delta('Start')); messages.dispatch('wheel');
  messages.scrollTop = 300; messages.dispatch('scroll');
  window.innerWidth = 390; window.visualViewport.height = 400;
  window.visualViewport.dispatch('resize');
  assert.equal(messages.scrollTop,300);
  window.visualViewport.scale = 2; window.visualViewport.height = 200;
  window.visualViewport.dispatch('resize');
  assert.equal(document.body.style.height,'400px');
});

test('long-running activity tip updates text with elapsed time and progressive hints', () => {
  const {ui, get} = setup();
  const tip = get('pi-activity-tip'), label = get('pi-activity-text');

  // 1. Thinking progression
  ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_start'}});
  assert.equal(tip.hidden, false);
  assert.equal(label.textContent, 'Thinking…');
  ui.updateActivityDisplay(6);
  assert.equal(label.textContent, 'Thinking… (6s)');
  ui.updateActivityDisplay(18);
  assert.equal(label.textContent, 'Thinking deeply, analyzing the problem… (18s)');
  ui.updateActivityDisplay(35);
  assert.equal(label.textContent, 'Still thinking, working through complex steps… (35s)');

  // 2. Tool progression
  ui.event({type: 'tool_execution_start', toolCallId: 't1', toolName: 'web_search', args: {}});
  assert.equal(label.textContent, 'Running web_search…');
  ui.updateActivityDisplay(7);
  assert.equal(label.textContent, 'Running web_search… (7s)');
  ui.updateActivityDisplay(16);
  assert.equal(label.textContent, 'Still executing web_search… (16s)');
  ui.updateActivityDisplay(32);
  assert.equal(label.textContent, 'Tool web_search is still running, waiting for results… (32s)');

  // 3. Clear on completion
  ui.event({type: 'tool_execution_end', toolCallId: 't1', toolName: 'web_search', result: {}});
  assert.equal(label.textContent, 'Preparing a response…');
  ui.updateActivityDisplay(8);
  assert.equal(label.textContent, 'Preparing a response… (8s)');
  ui.event(delta('Final response text'));
  assert.equal(tip.hidden, true);
});

test('repeated streaming events preserve elapsed activity start time across an advancing clock', () => {
  let currentTime = 1000000;
  const originalNow = Date.now;
  Date.now = () => currentTime;
  try {
    const {ui, get} = setup();
    const tip = get('pi-activity-tip'), label = get('pi-activity-text');

    // Start thinking
    ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_start'}});
    assert.equal(tip.hidden, false);
    assert.equal(label.textContent, 'Thinking…');

    // Repeated thinking deltas every 2 seconds
    currentTime += 2000;
    ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_delta', contentIndex: 0, delta: 'a'}});
    assert.equal(label.textContent, 'Thinking…');

    currentTime += 4000; // total 6s
    ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_delta', contentIndex: 0, delta: 'b'}});
    assert.equal(label.textContent, 'Thinking… (6s)');

    currentTime += 10000; // total 16s
    ui.event({type: 'message_update', assistantMessageEvent: {type: 'thinking_delta', contentIndex: 0, delta: 'c'}});
    assert.equal(label.textContent, 'Thinking deeply, analyzing the problem… (16s)');

    // Tool start resets timer for the new activity
    currentTime += 2000;
    ui.event({type: 'tool_execution_start', toolCallId: 't1', toolName: 'bash', args: {}});
    assert.equal(label.textContent, 'Running bash…');

    currentTime += 7000; // 7s of tool
    ui.event({type: 'tool_execution_update', toolCallId: 't1', toolName: 'bash', partialResult: 'output'});
    assert.equal(label.textContent, 'Running bash… (7s)');

    // Clearing activity resets state
    ui.event(delta('Answer arrived'));
    assert.equal(tip.hidden, true);
  } finally {
    Date.now = originalNow;
  }
});

test('assistant output deliverables render download pills in message bubble and trigger downloads', async () => {
  let downloadedPath = null;
  const mockFetch = async (url) => {
    if (url.includes('/files/outputs/')) {
      downloadedPath = url;
      return {
        ok: true,
        blob: async () => ({size: 1234})
      };
    }
    return {ok: true, json: async () => ({})};
  };

  const {ui, get} = setup(mockFetch);
  ui.setCurrent('sess-123');

  // Assistant stream with generated deliverable attached at message_end
  ui.event(delta('I have created the requested file.'));
  ui.event({
    type: 'message_end',
    message: {
      role: 'assistant',
      content: [{type: 'text', text: 'I have created the requested file.'}],
      attachments: ['outputs/analysis.csv']
    }
  });

  const messages = get('pi-messages');
  const bubbleWrap = messages.querySelector('.pi-bubble-wrap');
  assert.ok(bubbleWrap, 'Bubble wrap should exist');
  const attachBox = bubbleWrap.querySelector('.pi-msg-attachments');
  assert.ok(attachBox, 'Attachment box should exist in assistant bubble');
  const pills = attachBox.querySelectorAll('.badge');
  assert.equal(pills.length, 1);
  assert.ok(pills[0].textContent.includes('📥'));

  const link = pills[0].querySelector('a');
  assert.ok(link, 'Link element should exist in pill');
  assert.equal(link.textContent, 'analysis.csv');
  assert.equal(link.getAttribute('data-path'), 'outputs/analysis.csv');

  // Trigger download click
  let defaultPrevented = false;
  await link.onclick({preventDefault: () => { defaultPrevented = true; }});
  assert.equal(defaultPrevented, true);
  assert.ok(downloadedPath.includes('/files/outputs/analysis.csv'), 'Should fetch file from correct endpoint');
  assert.equal(get('pi-status').textContent, 'Download ready: analysis.csv');
});

test('in-dialog markdown links to outputs are intercepted and trigger file downloads', async () => {
  let downloadedPath = null;
  const mockFetch = async (url) => {
    if (url.includes('/files/outputs/')) {
      downloadedPath = url;
      return {
        ok: true,
        blob: async () => ({size: 5678})
      };
    }
    return {ok: true, json: async () => ({})};
  };

  const {ui, get} = setup(mockFetch);
  ui.setCurrent('sess-456');

  const body = ui.bubble('Assistant', '');
  // Simulate marked parse generating an anchor tag to outputs/chart.png
  const anchor = new Element('a');
  anchor.setAttribute('href', 'outputs/chart.png');
  anchor.textContent = 'Download chart';
  body.append(anchor);
  body.querySelectorAll = (sel) => sel === 'a' ? [anchor] : [];

  ui.render(body, '[Download chart](outputs/chart.png)');

  assert.equal(anchor.getAttribute('download'), 'chart.png');
  assert.equal(anchor.getAttribute('data-path'), 'outputs/chart.png');

  let defaultPrevented = false;
  await anchor.onclick({preventDefault: () => { defaultPrevented = true; }});
  assert.equal(defaultPrevented, true);
  assert.ok(downloadedPath.includes('/files/outputs/chart.png'));
  assert.equal(get('pi-status').textContent, 'Download ready: chart.png');
});

test('opening conversation restores output attachments and adds download pills to bubble', async () => {
  const detail = {
    session: {title: 'Test Session', settings: {model: 'test-model', thinking: 'off', prompt: 'general'}, prompt_text: ''},
    messages: [
      {role: 'user', content: 'Create a spreadsheet'},
      {role: 'assistant', content: 'Here is your spreadsheet', attachments: ['outputs/data.xlsx']}
    ],
    files: [{path: 'outputs/data.xlsx', size: 1024}]
  };

  const mockFetch = async (url) => {
    if (url.includes('/detail')) return {ok: true, json: async () => detail};
    if (url.includes('/options')) return {ok: true, json: async () => ({models: [], levels: []})};
    if (url.includes('/files')) return {ok: true, json: async () => detail.files};
    return {ok: true, json: async () => ({})};
  };

  const {ui, get} = setup(mockFetch);
  await ui.openConversation('sess-restored');

  const messages = get('pi-messages');
  const pills = messages.querySelectorAll('.badge');
  const outputPills = pills.filter(p => p.textContent.includes('📥'));
  assert.equal(outputPills.length, 1);
  const link = outputPills[0].querySelector('a');
  assert.equal(link.textContent, 'data.xlsx');
  assert.equal(link.getAttribute('data-path'), 'outputs/data.xlsx');
});

test('deduplicates attachments by full path so same-named files across distinct paths both render', () => {
  const {ui, get} = setup();
  const body = ui.bubble('You', 'Uploaded files', [
    'uploads/111111111111-report.csv',
    'uploads/222222222222-report.csv'
  ]);
  const messages = get('pi-messages');
  const attachBox = messages.querySelector('.pi-msg-attachments');
  assert.ok(attachBox);
  const links = attachBox.querySelectorAll('a');
  assert.equal(links.length, 2);
  assert.equal(links[0].getAttribute('data-path'), 'uploads/111111111111-report.csv');
  assert.equal(links[1].getAttribute('data-path'), 'uploads/222222222222-report.csv');
});

test('url-encoded relative markdown links are decoded before download path construction', async () => {
  let downloadedPath = null;
  const mockFetch = async url => {
    downloadedPath = url;
    return {ok: true, blob: async () => ({size: 100})};
  };
  const {ui} = setup(mockFetch);
  ui.setCurrent('sess-encode');

  const body = ui.bubble('Assistant', '');
  const anchor = new Element('a');
  anchor.setAttribute('href', 'outputs/report%20final.csv');
  anchor.textContent = 'download';
  body.append(anchor);
  body.querySelectorAll = sel => sel === 'a' ? [anchor] : [];

  ui.render(body, '[download](outputs/report%20final.csv)');
  assert.equal(anchor.getAttribute('data-path'), 'outputs/report final.csv');
  await anchor.onclick({preventDefault() {}});
  assert.equal(downloadedPath, '/api/pi/sessions/sess-encode/files/outputs/report%20final.csv');
});

test('handles malformed uri, query/fragment suffixes, and preserved session id in api links', async () => {
  let downloadedPath = null;
  const mockFetch = async url => {
    downloadedPath = url;
    return {ok: true, blob: async () => ({size: 100})};
  };
  const {ui} = setup(mockFetch);
  ui.setCurrent('sess-curr');

  // 1. Malformed percent escape should not throw and fall back cleanly
  const body1 = ui.bubble('Assistant', '');
  const a1 = new Element('a');
  a1.setAttribute('href', 'outputs/progress100%.csv');
  body1.querySelectorAll = sel => sel === 'a' ? [a1] : [];
  assert.doesNotThrow(() => ui.render(body1, '[progress](outputs/progress100%.csv)'));
  assert.equal(a1.getAttribute('data-path'), 'outputs/progress100%.csv');

  // 2. Fragment and query stripping from path
  const body2 = ui.bubble('Assistant', '');
  const a2 = new Element('a');
  a2.setAttribute('href', 'outputs/report.pdf#page=2');
  body2.querySelectorAll = sel => sel === 'a' ? [a2] : [];
  ui.render(body2, '[pdf](outputs/report.pdf#page=2)');
  assert.equal(a2.getAttribute('data-path'), 'outputs/report.pdf');
  await a2.onclick({preventDefault() {}});
  assert.equal(downloadedPath, '/api/pi/sessions/sess-curr/files/outputs/report.pdf');

  // 3. API link to another session preserves that session's id
  const body3 = ui.bubble('Assistant', '');
  const a3 = new Element('a');
  a3.setAttribute('href', '/api/pi/sessions/other-sess/files/outputs/doc.txt');
  body3.querySelectorAll = sel => sel === 'a' ? [a3] : [];
  ui.render(body3, '[doc](/api/pi/sessions/other-sess/files/outputs/doc.txt)');
  assert.equal(a3.getAttribute('data-path'), 'outputs/doc.txt');
  await a3.onclick({preventDefault() {}});
  assert.equal(downloadedPath, '/api/pi/sessions/other-sess/files/outputs/doc.txt');
});

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ''; this.dataset = {}; this.value = ''; this.style = {}; this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0; }
  addEventListener(name, callback) { this['on' + name] = callback; }
  showModal() { this.open = true; }
  close(value = '') { this.returnValue = value; this.open = false; this.onclose?.(); }
  focus() {}
  select() {}
  add(option) { this.append(option); }
  setAttribute(name,value) { this[name] = value; }
  append(...nodes) { nodes.forEach(node => {node.parentElement = this; this.children.push(node);}); }
  replaceWith(node) { const p = this.parentElement; p.children[p.children.indexOf(this)] = node; node.parentElement = p; }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
}
function setup(fetch, storedProject = null) {
  const stored = new Map();
  if (storedProject) stored.set('coding-agent-project-default', storedProject);
  const elements = new Map(), copied = [], observers = [];
  class Observer { constructor(callback) { observers.push(callback); } observe() {} }
  const clipboard = {writeText:async text => {copied.push(text);}};
  const get = id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); };
  const context = {fetch, Option:class extends Element { constructor(text, value) { super('option'); this.textContent = text; this.value = value; } }, MutationObserver:Observer, ResizeObserver:Observer, localStorage:{getItem:key => stored.get(key) ?? null,setItem:(key,value) => stored.set(key,value)}, navigator:{clipboard}, TextDecoder, setTimeout, clearTimeout, marked:require('../static/js/marked.min.js'), DOMPurify:{sanitize:html => 'sanitized:' + html}, document: {getElementById:get, querySelectorAll:() => [get('coding-jump-latest'), get('coding-sidebar-toggle')], createElement:tag => new Element(tag)}, window:{AppI18n:{t:(s,v) => s.replace(/\{(\w+)\}/g, (match, key) => v?.[key] ?? match)}}};
  vm.createContext(context);
  vm.runInContext(readFileSync('static/js/coding-agent.js','utf8').replace("  initialize().catch(report);", '  window.test = {tool, render, newTurn, browse, updateMarkdown, send, thinking, initialize, activityEvent, setBusy};'),context);
  return {observers, stored, copied, clipboard, ui:context.window.test, window:context.window, get, messages:get('coding-messages')};
}
test('input label shows agent activity and resets after completion or failure', () => {
  const {ui, get} = setup();
  ui.activityEvent({type:'message_update', assistantMessageEvent:{type:'thinking_start',contentIndex:0}});
  assert.equal(get('coding-activity').textContent, 'Thinking…');
  assert.equal(get('coding-activity-spinner').hidden, false);
  ui.activityEvent({type:'tool_execution_start',toolCallId:'a',toolName:'read'});
  assert.equal(get('coding-activity').textContent, 'Running a tool…');
  ui.activityEvent({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Hello'}});
  assert.equal(get('coding-activity').textContent, 'Preparing a response…');
  ui.activityEvent({type:'done'});
  assert.equal(get('coding-activity').textContent, '');
  assert.equal(get('coding-activity-spinner').hidden, true);
  ui.activityEvent({type:'tool_execution_start',toolCallId:'b',toolName:'read'});
  ui.setBusy(false);
  assert.equal(get('coding-activity').textContent, '');
  assert.equal(get('coding-activity-spinner').hidden, true);
});

test('jump to latest scrolls history to the bottom and stays usable while busy', () => {
  const {get, messages, ui, observers} = setup();
  const button = get('coding-jump-latest');
  assert.equal(button.hidden, true);
  messages.scrollTop = 0;
  messages.clientHeight = 600;
  messages.scrollHeight = 2400;
  observers[0]();
  assert.equal(button.hidden, false);
  messages.scrollTop = 1800;
  messages.onscroll();
  assert.equal(button.hidden, true);
  messages.scrollTop = 500;
  messages.onscroll();
  assert.equal(button.hidden, false);
  ui.setBusy(true);
  const jump = get('coding-jump-latest');
  assert.equal(jump.disabled, false);
  jump.onclick();
  assert.equal(messages.scrollTop, 2400);
  assert.equal(jump.hidden, true);
  messages.scrollHeight = 3600;
  observers[0]();
  assert.equal(jump.hidden, false);
  jump.onclick();
  assert.equal(messages.scrollTop, 3600);
  messages.scrollHeight = 5000;
  observers[1]();
  assert.equal(jump.hidden, false);
  messages.scrollHeight = 0;
  messages.scrollTop = 0;
  observers[0]();
  assert.equal(jump.hidden, true);
});

test('eleventh tool groups calls and preserves arguments and output', () => {
  const {ui,messages} = setup();
  for (let i=0;i<10;i++) ui.tool(String(i),'read','args '+i);
  assert.equal(messages.children[0].tagName,'div');
  ui.tool('10','bash','command');
  const wrapper = messages.children[0];
  assert.equal(wrapper.tagName,'details');
  assert.equal(wrapper.children[0].textContent,'Tool calls (11)');
  assert.equal(wrapper.children[1].children.length,11);
  ui.tool('10','bash','result');
  assert.equal(wrapper.children[1].children[10].children[1].textContent,'command\n\nresult');
  ui.newTurn(); ui.tool('next','read','next');
  assert.equal(messages.children[1].tagName,'div');
});
test('saved calls use the same grouping and render untrusted output as text', () => {
  const {ui,messages} = setup();
  ui.render({role:'user',content:'hello'});
  ui.render({role:'assistant',content:Array.from({length:11},(_,i)=>({type:'toolCall',id:String(i),name:'read',arguments:{path:'<script>'}}))});
  ui.render({role:'toolResult',toolCallId:'0',toolName:'read',content:'<img onerror=alert(1)>'});
  const group = messages.children[1];
  assert.equal(group.tagName,'details');
  assert.match(group.children[1].children[0].children[1].textContent, /<img onerror/);
});

test('selected path loads its history even when Pi options fail', async () => {
  const urls = [];
  const {ui,get} = setup(async url => {
    urls.push(url);
    if (url.includes('/options')) throw new Error('Options unavailable');
    return {ok:true,json:async () => url.includes('/projects') ? {path:'/home/dev/project',parent:'/home/dev',folders:[]} : [{id:'saved',title:'Existing Pi history',project:'/home/dev/project'}]};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  assert.match(urls[1], /sessions\?project=%2Fhome%2Fdev%2Fproject/);
  assert.equal(get('coding-history').children[0].children[0].textContent,'Existing Pi history');
  assert.equal(get('coding-project-name').textContent,'/home/dev/project');
  let selected = false, prevented = false;
  get('coding-select').onclick = () => {selected = true;};
  get('coding-path').onkeydown({key:'Enter',preventDefault:() => {prevented = true;}});
  assert.equal(selected,true); assert.equal(prevented,true);
});

test('message shortcuts submit for Cmd or Ctrl and preserve plain Enter and IME input', () => {
  const {get} = setup();
  let submissions = 0, prevented = 0;
  get('coding-form').requestSubmit = () => { submissions++; };
  get('coding-message').value = 'Hello';
  const press = extra => get('coding-message').onkeydown({key:'Enter',preventDefault:() => {prevented++;},...extra});
  press({metaKey:true});
  press({ctrlKey:true});
  assert.equal(submissions,2);
  assert.equal(prevented,2);
  press({});
  press({ctrlKey:true,isComposing:true});
  press({metaKey:true,repeat:true});
  press({ctrlKey:true,key:'a'});
  assert.equal(submissions,2);
  assert.equal(prevented,2);
  get('coding-message').value = '   ';
  press({ctrlKey:true});
  assert.equal(submissions,2);
});

test('user labels are removed and messages render sanitized Markdown including streaming updates', () => {
  const {ui,messages} = setup();
  ui.render({role:'user',content:'**Hello**'});
  const user = messages.children[0];
  assert.match(user.className, /coding-user-message/);
  assert.equal(user.children.length,1);
  assert.equal(user.children[0].innerHTML,'sanitized:<p><strong>Hello</strong></p>\n');
  ui.render({role:'assistant',content:'## Heading\n\n- item\n\n```js\nconst x = 1;\n```'});
  const body = messages.children[1].children[1];
  assert.match(body.innerHTML, /^sanitized:<h2>Heading<\/h2>/);
  assert.match(body.innerHTML, /<ul>/);
  assert.match(body.innerHTML, /<code class="language-js">/);
  ui.updateMarkdown(body, '**Partial');
  ui.updateMarkdown(body, body.dataset.markdown + '**');
  assert.equal(body.innerHTML,'sanitized:<p><strong>Partial</strong></p>\n');
});

test('sent messages render once despite Pi echo, while repeated sends remain separate', async () => {
  const frames = [
    {type:'message_end',message:{role:'user',content:'hello'}},
    {type:'message_end',message:{role:'assistant',content:'response'}},
    {type:'done'},
  ].map(event => 'data: ' + JSON.stringify(event) + '\n\n').join('');
  let sent = 0;
  const {ui,messages} = setup(async (url, request) => {
    if (url.includes('/options')) throw new Error('Options unavailable');
    if (url.endsWith('/messages')) {
      sent++;
      let read = false;
      return {ok:true,body:{getReader:() => ({read:async () => {
        if (read) return {done:true};
        read = true; return {value:new TextEncoder().encode(frames),done:false};
      }})}};
    }
    return {ok:true,json:async () => url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : request.method === 'POST' ? {id:'session'} : []};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  await ui.send('hello');
  assert.equal(messages.children.length,2);
  assert.match(messages.children[0].className,/coding-user-message/);
  assert.equal(messages.children[1].children[1].dataset.markdown,'response');
  await ui.send('hello');
  assert.equal(sent,2);
  assert.equal(messages.children.length,4);
  messages.replaceChildren();
  ui.render({role:'user',content:'hello'});
  ui.render({role:'user',content:'hello'});
  assert.equal(messages.children.length,2);
});

test('running tasks accept one append request, retain newer drafts, and render follow-ups once', async () => {
  let releaseStream, releaseAppend, notifyActive;
  const active = new Promise(resolve => {notifyActive = resolve;});
  const streamGate = new Promise(resolve => {releaseStream = resolve;});
  const appendGate = new Promise(resolve => {releaseAppend = resolve;});
  const requests = [];
  const {ui,get,messages} = setup(async (url, request) => {
    if (url.includes('/options')) throw new Error('Options unavailable');
    if (url.endsWith('/append')) {
      requests.push(JSON.parse(request.body));
      if (requests.at(-1).message === 'retry') return {ok:false,json:async () => ({error:'Pi is no longer running. Send your message again.'})};
      await appendGate;
      return {ok:true};
    }
    if (url.endsWith('/messages')) {
      let finished = false;
      return {ok:true,body:{getReader:() => ({read:async () => {
        if (finished) return {done:true};
        notifyActive(); await streamGate; finished = true;
        const events = [{type:'message_end',message:{role:'user',content:'follow-up'}},{type:'message_end',message:{role:'assistant',content:'Done'}}];
        return {done:false,value:new TextEncoder().encode(events.map(e => 'data: '+JSON.stringify(e)+'\n\n').join(''))};
      }})}};
    }
    return {ok:true,json:async () => url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : request.method === 'POST' ? {id:'session'} : []};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  get('coding-message').value = 'initial';
  const task = ui.send('initial');
  await active;
  assert.equal(get('coding-message').disabled,false);
  assert.equal(get('coding-send').disabled,false);
  get('coding-message').value = 'follow-up';
  const append = ui.send('follow-up');
  get('coding-message').value = 'new draft';
  await ui.send('duplicate');
  releaseAppend(); await append;
  assert.equal(requests.length,1);
  assert.equal(requests[0].message,'follow-up');
  assert.equal(messages.children.length,2);
  assert.equal(get('coding-message').value,'new draft');
  let shortcuts = 0;
  get('coding-form').requestSubmit = () => {shortcuts++;};
  get('coding-message').onkeydown({key:'Enter',ctrlKey:true,preventDefault(){}});
  assert.equal(shortcuts,1);
  get('coding-message').value = 'retry';
  await assert.rejects(ui.send('retry'), /Pi is no longer running/);
  assert.equal(get('coding-message').value,'retry');
  assert.equal(get('coding-send').disabled,false);
  assert.equal(messages.children.length,2);
  get('coding-message').value = 'new draft';
  releaseStream(); await task;
  assert.equal(messages.children.length,3);
  assert.equal(get('coding-message').value,'new draft');
});

test('history deletion asks for confirmation and refreshes only after success', async () => {
  let deleted = false, writes = 0;
  const {ui, get} = setup(async (url, request) => {
    if (url.includes('/options')) throw new Error('Options unavailable');
    if (url.endsWith('/delete')) { writes++; deleted = true; assert.equal(request.method, 'POST'); return {ok:true}; }
    return {ok:true,json:async () => url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : deleted ? [] : [{id:'saved',project:'/home/dev/project',title:'<script>Title</script>'}]};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  const remove = get('coding-history').children[0].children[1];
  assert.match(remove['aria-label'], /<script>Title<\/script>/);
  assert.equal(remove.children[0].textContent, '×');
  assert.match(remove.className, /coding-history-delete/);
  let pending = remove.onclick();
  assert.equal(get('coding-delete-dialog').open, true);
  assert.match(get('coding-delete-description').textContent, /<script>Title<\/script>/);
  assert.match(get('coding-delete-description').textContent, /Pi transcript files will be preserved/);
  get('coding-delete-cancel').onclick();
  await pending;
  assert.equal(writes, 0);
  pending = remove.onclick();
  get('coding-delete-dialog').close(); // Escape/close without confirmation.
  await pending;
  assert.equal(writes, 0);
  pending = remove.onclick();
  get('coding-delete-confirm').onclick();
  await pending;
  assert.equal(writes, 1);
  assert.equal(get('coding-history').children.length, 0);
  assert.equal(get('coding-status').textContent, 'Conversation deleted from history.');
});

test('mobile project/history navigation can collapse even while the agent is busy', () => {
  const {get, ui} = setup();
  const toggle = get('coding-sidebar-toggle'), sidebar = get('coding-sidebar');
  toggle.onclick();
  assert.equal(sidebar.dataset.mobileOpen, 'true');
  assert.equal(toggle['aria-expanded'], 'true');
  ui.setBusy(true);
  assert.equal(toggle.disabled, false);
  toggle.onclick();
  assert.equal(sidebar.dataset.mobileOpen, 'false');
  assert.equal(toggle['aria-expanded'], 'false');
  ui.setBusy(false);
  toggle.onclick();
  get('coding-new').onclick();
  assert.equal(sidebar.dataset.mobileOpen, 'false');
});

test('mobile navigation can choose/create folders and load model, thinking and command controls', async () => {
  const requests = [];
  const {get, ui, stored} = setup(async (url, request) => {
    requests.push({url, request});
    let data = [];
    if (url.includes('/projects')) data = {path:'/home/dev/project',parent:'/home/dev',folders:[]};
    if (url.includes('/options')) data = {models:[{provider:'test',id:'model'}],levels:['low','high'],commands:[{name:'review',description:'Review'}]};
    return {ok:true,json:async () => data};
  });
  await ui.initialize();
  get('coding-sidebar-toggle').onclick();
  await get('coding-directory').onclick();
  assert.equal(get('coding-directory-dialog').open, true);
  get('coding-folder-name').value = 'new-folder';
  await get('coding-create').onclick();
  assert.equal(JSON.parse(requests.at(-1).request.body).name, 'new-folder');
  await get('coding-select').onclick();
  assert.equal(get('coding-directory-dialog').open, false);
  assert.equal(get('coding-sidebar').dataset.mobileOpen, 'false');
  assert.equal(stored.get('coding-agent-project-default'), '/home/dev/project');
  get('coding-model').value = 'test/model';
  await get('coding-model').onchange();
  assert.equal(get('coding-model').value, 'test/model');
  assert.equal(get('coding-thinking').children.at(-1).value, 'high');
  assert.equal(get('coding-command').children.at(-1).value, 'review');
  get('coding-command').value = 'reload';
  await get('coding-command').onchange();
  assert.equal(get('coding-status').textContent, 'Project commands and settings reloaded.');
});

test('history titles rename with touch button, double-click, keyboard and blur', async () => {
  let title = 'Original', writes = 0;
  const {ui,get} = setup(async (url, request) => {
    if (url.includes('/options')) throw new Error('Options unavailable');
    if (url.endsWith('/name')) { title = JSON.parse(request.body).name; writes++; return {ok:true,json:async () => ({title})}; }
    return {ok:true,json:async () => url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : [{id:'saved',project:'/home/dev/project',title}]};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  const row = get('coding-history').children[0], button = row.children[0];
  assert.equal(row.children.length,3);
  const edit = row.children[2];
  assert.equal(edit['aria-label'], 'Rename conversation: Original');
  edit.onclick();
  let input = row.children[3];
  assert.equal(input.tagName,'input');
  input.value = 'Discard';
  const cancel = row.children[4].children[1];
  let preventedBlur = false;
  cancel.onpointerdown({preventDefault() { preventedBlur = true; }});
  assert.equal(preventedBlur, true);
  cancel.onclick();
  assert.equal(writes,0);
  assert.equal(row.children.length,3);
  button.ondblclick(); input = row.children[3]; input.value = 'Renamed';
  await input.onkeydown({key:'Enter',preventDefault(){}});
  assert.equal(writes,1);
  assert.equal(button.textContent,'Renamed');
  assert.equal(row.children.length,3);
  assert.equal(edit['aria-label'], 'Rename conversation: Renamed');
  button.onpointerup({pointerType:'touch'});
  button.onpointerup({pointerType:'touch'});
  input = row.children[3];
  assert.equal(input.tagName,'input');
  input.onkeydown({key:'Escape',preventDefault(){}});
  button.onkeydown({key:'F2',preventDefault(){}});
  input = row.children[3]; input.value = 'Saved on blur';
  await input.onblur();
  assert.equal(writes,2);
  assert.equal(button.textContent,'Saved on blur');
  edit.onclick();
  row.children[3].value = 'Saved by touch';
  await row.children[4].children[0].onclick();
  assert.equal(writes,3);
  assert.equal(button.textContent,'Saved by touch');
  assert.equal(row.dataset.editing, undefined);

});

test('thinking shares the tool group and counts toward its collapse threshold', () => {
  const {ui,messages} = setup();
  ui.render({role:'assistant',content:[{type:'thinking',thinking:'Reasoning <script>'},...Array.from({length:10},(_,i)=>({type:'toolCall',id:String(i),name:'read',arguments:{}}))]});
  assert.equal(messages.children.length,1);
  const group = messages.children[0];
  assert.equal(group.tagName,'details');
  assert.equal(group.children[0].textContent,'Tool calls (11)');
  assert.equal(group.children[1].children.length,11);
  assert.equal(group.children[1].children[0].children[1].textContent,'Reasoning <script>');
});

test('thinking deltas and final saved blocks update the same tool card', () => {
  const {ui,messages} = setup();
  ui.thinking(0,'First ',true);
  ui.thinking(0,'thought',true);
  const card = messages.children[0].children[0];
  assert.equal(card.children[1].textContent,'First thought');
  ui.render({role:'assistant',content:[{type:'thinking',thinking:'Final thought'}]},true);
  assert.equal(messages.children[0].children.length,1);
  assert.equal(card.children[1].textContent,'Final thought');
  ui.render({role:'assistant',content:[{type:'thinking',thinking:'Next thought'}]});
  assert.equal(messages.children[0].children.length,2);
});

test('expanded tool cards collapse on double-click and copy their latest text', async () => {
  const {ui,messages,copied,clipboard,get} = setup();
  ui.tool('tool','read','arguments');
  const card = messages.children[0].children[0];
  card.open = true;
  let stopped = false;
  card.ondblclick({target:{closest:selector => selector === 'details' ? card : null},stopPropagation(){stopped = true;}});
  assert.equal(card.open,false); assert.equal(stopped,true);
  card.open = true;
  card.ondblclick({target:{closest:selector => selector === 'details' ? card : {}},stopPropagation(){}});
  assert.equal(card.open,true);
  ui.tool('tool','read','output');
  await card.children[2].onclick({stopPropagation(){}});
  assert.deepEqual(copied,['arguments\n\noutput']);
  ui.render({role:'assistant',content:'**Markdown**'});
  await messages.children[1].children[2].onclick({stopPropagation(){}});
  assert.equal(copied[1],'**Markdown**');
  clipboard.writeText = async () => {throw new Error('denied');};
  await card.children[2].onclick({stopPropagation(){}});
  assert.match(get('coding-status').textContent,/Unable to copy/);
});

test('grouped thinking and tools can be copied together and collapsed', async () => {
  const {ui,messages,copied} = setup();
  ui.thinking(0,'Reasoning');
  for (let i=0;i<10;i++) ui.tool(String(i),'read','output '+i);
  const wrapper = messages.children[0];
  wrapper.open = true;
  wrapper.ondblclick({target:{closest:selector => selector === 'details' ? wrapper : null},stopPropagation(){}});
  assert.equal(wrapper.open,false);
  await wrapper.children[2].onclick({stopPropagation(){}});
  assert.match(copied[0],/Reasoning/); assert.match(copied[0],/output 9/);
});

test('history opens even when loading saved model options fails', async () => {
  const {ui,get,messages} = setup(async url => {
    if (url.includes('/options')) throw new Error('Options unavailable');
    const data = url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : url.endsWith('/detail') ? {session:{settings:{model:'old/model',thinking:'high'}},messages:[{role:'user',content:'Saved question'},{role:'assistant',content:'Saved answer'}]} : [{id:'saved',project:'/home/dev/project',title:'History'}];
    return {ok:true,json:async () => data};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  await get('coding-history').children[0].children[0].onclick({detail:0});
  assert.equal(messages.children.length,2);
  assert.equal(messages.children[1].children[1].dataset.markdown,'Saved answer');
});

test('reload restores the selected project and its history', async () => {
  const requested = [];
  const fetch = async url => {
    requested.push(url);
    if (url.includes('/options')) throw new Error('Options unavailable');
    return {ok:true,json:async () => url.includes('/projects') ? {path:'/home/dev/new-project',folders:[]} : [{id:'saved',project:'/home/dev/new-project',title:'New project chat'}]};
  };
  const first = setup(fetch);
  await assert.rejects(first.ui.browse('/home/dev/new-project'),/Options unavailable/);
  const saved = first.stored.get('coding-agent-project-default');
  assert.equal(saved,'/home/dev/new-project');
  requested.length = 0;
  const reloaded = setup(fetch,saved);
  await assert.rejects(reloaded.ui.initialize(),/Options unavailable/);
  assert.match(requested[0],/path=%2Fhome%2Fdev%2Fnew-project/);
  assert.equal(reloaded.get('coding-history').children[0].children[0].textContent,'New project chat');
});

test('opening a managed active session replays its raw stream without sending a new message', async () => {
  let posted = 0;
  const events = [{type:'api_user_message',message:'Current request'},
    {type:'api_user_message',message:'Appended request'},
    {type:'message_end',message:{role:'assistant',content:'Finished response'}},{type:'done'}];
  const {ui,get,messages} = setup(async (url,request) => {
    if (request.method === 'POST') posted++;
    if (url.includes('/options')) throw new Error('Options unavailable');
    if (url.endsWith('/events')) {
      let done = false;
      return {ok:true,body:{getReader:() => ({read:async () => {
        if(done)return {done:true}; done=true;
        return {done:false,value:new TextEncoder().encode(events.map(e => 'data: '+JSON.stringify(e)+'\n\n').join(''))};
      }})}};
    }
    const data = url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : url.endsWith('/detail') ? {
      session:{settings:{model:'test/model',thinking:'high'}},active_run:{id:'run-active',user_message_id:'current'},
      messages:[{role:'user',content:'Older request'},{id:'current',role:'user',content:'Current request',run_id:'run-active'},
        {role:'user',content:'Appended request'}]} : [{id:'saved',project:'/home/dev/project',title:'Running'}];
    return {ok:true,json:async () => data};
  });
  await assert.rejects(ui.browse('/home/dev/project'),/Options unavailable/);
  await get('coding-history').children[0].children[0].onclick({detail:0});
  assert.equal(posted,0);
  assert.equal(messages.children.length,4);
  assert.equal(messages.children[1].children[0].dataset.markdown,'Current request');
  assert.equal(messages.children[2].children[0].dataset.markdown,'Appended request');
  assert.equal(messages.children[3].children[1].dataset.markdown,'Finished response');
});

test('live thinking and tool cards open while working and close when finished', () => {
  const {ui,messages} = setup();
  ui.activityEvent({type:'message_update',assistantMessageEvent:{type:'thinking_delta',contentIndex:0,delta:'Planning'}});
  const thinking = messages.children[0].children[0];
  assert.equal(thinking.open,true);
  assert.equal(thinking.children[1].textContent,'Planning');
  ui.activityEvent({type:'message_update',assistantMessageEvent:{type:'thinking_end',contentIndex:0}});
  assert.equal(thinking.open,false);
  ui.activityEvent({type:'tool_execution_start',toolCallId:'t',toolName:'bash',args:{command:'pwd'}});
  const tool = messages.children[0].children[1];
  assert.equal(tool.open,true);
  ui.activityEvent({type:'tool_execution_update',toolCallId:'t',toolName:'bash',partialResult:{content:'partial'}});
  ui.activityEvent({type:'tool_execution_end',toolCallId:'t',toolName:'bash',result:{content:'finished'}});
  assert.equal(tool.open,false);
  assert.match(tool.children[1].textContent,/pwd/);
  assert.match(tool.children[1].textContent,/finished/);
  assert.doesNotMatch(tool.children[1].textContent,/partial/);
  ui.render({role:'assistant',content:'Final answer'});
  const group = messages.children[0];
  assert.equal(group.tagName,'details');
  assert.equal(group.open,false);
  assert.equal(group.children[0].textContent,'Tool calls (2)');
});

test('responses collapse even one activity and later active tools remain visible', () => {
  const {ui,messages} = setup();
  ui.activityEvent({type:'tool_execution_start',toolCallId:'a',toolName:'read',args:{}});
  ui.activityEvent({type:'tool_execution_end',toolCallId:'a',toolName:'read',result:{content:'done'}});
  ui.activityEvent({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Response'}});
  assert.equal(messages.children[0].open,false);
  ui.render({role:'assistant',content:'Response'});
  const group = messages.children[0];
  assert.equal(group.open,false);
  assert.equal(group.children[0].textContent,'Tool calls (1)');
  ui.activityEvent({type:'tool_execution_start',toolCallId:'b',toolName:'read',args:{}});
  assert.equal(group.open,true);
  ui.activityEvent({type:'tool_execution_start',toolCallId:'c',toolName:'read',args:{}});
  ui.activityEvent({type:'tool_execution_end',toolCallId:'b',toolName:'read',result:{}});
  assert.equal(group.open,true);
  ui.activityEvent({type:'tool_execution_end',toolCallId:'c',toolName:'read',result:{}});
  assert.equal(group.open,false);
  ui.render({role:'assistant',content:'Another response'});
  assert.equal(group.children[0].textContent,'Tool calls (3)');
});

test('saved activity is grouped before its response even below ten calls', () => {
  const {ui,messages} = setup();
  ui.render({role:'assistant',content:[{type:'thinking',thinking:'Saved reasoning'},{type:'text',text:'Saved reply'}]});
  assert.equal(messages.children[0].tagName,'details');
  assert.equal(messages.children[0].open,false);
  assert.equal(messages.children[1].children[1].dataset.markdown,'Saved reply');
});

test('grouped parallel work stays expanded until the last tool finishes, and errors close pending work', () => {
  const {ui,messages} = setup();
  for(let i=0;i<11;i++) ui.activityEvent({type:'tool_execution_start',toolCallId:String(i),toolName:'read',args:{}});
  const group = messages.children[0];
  assert.equal(group.open,true);
  for(let i=0;i<10;i++) ui.activityEvent({type:'tool_execution_end',toolCallId:String(i),toolName:'read',result:{}});
  assert.equal(group.open,true);
  ui.activityEvent({type:'tool_execution_end',toolCallId:'10',toolName:'read',result:{}});
  assert.equal(group.open,false);
  ui.activityEvent({type:'tool_execution_start',toolCallId:'failed',toolName:'bash',args:{}});
  assert.equal(group.open,true);
  ui.activityEvent({type:'error',message:'Stopped'});
  assert.equal(group.open,false);
  assert.equal(group.children[1].children.at(-1).open,false);
});

test('opening history highlights the active conversation and resets on new chat', async () => {
  const {ui,get} = setup(async url => {
    if (url.includes('/options')) throw new Error('Options unavailable');
    const data = url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : url.endsWith('/detail') ? {session:{settings:{model:'test/model',thinking:''}},messages:[]} : [{id:'sess-1',project:'/home/dev/project',title:'Chat 1'},{id:'sess-2',project:'/home/dev/project',title:'Chat 2'}];
    return {ok:true,json:async () => data};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  get('coding-sidebar-toggle').onclick();
  assert.equal(get('coding-sidebar').dataset.mobileOpen, 'true');
  const rows = get('coding-history').children;
  assert.equal(rows.length, 2);
  const btn1 = rows[0].children[0], btn2 = rows[1].children[0];
  assert.equal(btn1['aria-current'], undefined);
  await btn1.onclick({detail:0});
  assert.equal(get('coding-sidebar').dataset.mobileOpen, 'false');
  assert.equal(btn1['aria-current'], 'true');
  assert.equal(btn2['aria-current'], 'false');
  await btn2.onclick({detail:0});
  assert.equal(btn1['aria-current'], 'false');
  assert.equal(btn2['aria-current'], 'true');
  get('coding-new').onclick();
  assert.equal(btn1['aria-current'], 'false');
  assert.equal(btn2['aria-current'], 'false');
});

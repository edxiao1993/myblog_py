const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ''; this.dataset = {}; this.value = ''; this.style = {}; }
  focus() {}
  select() {}
  setAttribute(name,value) { this[name] = value; }
  append(...nodes) { nodes.forEach(node => {node.parentElement = this; this.children.push(node);}); }
  replaceWith(node) { const p = this.parentElement; p.children[p.children.indexOf(this)] = node; node.parentElement = p; }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
}
function setup(fetch) {
  const elements = new Map(), copied = [];
  const clipboard = {writeText:async text => {copied.push(text);}};
  const get = id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); };
  const context = {fetch, navigator:{clipboard}, TextDecoder, setTimeout, clearTimeout, marked:require('../static/js/marked.min.js'), DOMPurify:{sanitize:html => 'sanitized:' + html}, document: {getElementById:get, querySelectorAll:() => [], createElement:tag => new Element(tag)}, window:{AppI18n:{t:(s,v) => s.replace('{count}',v?.count)}}};
  vm.createContext(context);
  vm.runInContext(readFileSync('static/js/coding-agent.js','utf8').replace("  run(() => browse(''))();", '  window.test = {tool, render, newTurn, browse, updateMarkdown, send, thinking};'),context);
  return {copied, clipboard, ui:context.window.test, window:context.window, get, messages:get('coding-messages')};
}
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

test('history titles rename inline on double-click with Enter and cancel with Escape', async () => {
  let title = 'Original', writes = 0;
  const {ui,get} = setup(async (url, request) => {
    if (url.includes('/options')) throw new Error('Options unavailable');
    if (url.endsWith('/name')) { title = JSON.parse(request.body).name; writes++; return {ok:true,json:async () => ({title})}; }
    return {ok:true,json:async () => url.includes('/projects') ? {path:'/home/dev/project',folders:[]} : [{id:'saved',project:'/home/dev/project',title}]};
  });
  await assert.rejects(ui.browse('/home/dev/project'), /Options unavailable/);
  const row = get('coding-history').children[0], button = row.children[0];
  assert.equal(row.children.length,1);
  button.ondblclick();
  let input = row.children[1];
  assert.equal(input.tagName,'input');
  input.value = 'Discard';
  input.onkeydown({key:'Escape',preventDefault(){}});
  assert.equal(writes,0);
  assert.equal(row.children.length,1);
  button.ondblclick(); input = row.children[1]; input.value = 'Renamed';
  await input.onkeydown({key:'Enter',preventDefault(){}});
  assert.equal(writes,1);
  assert.equal(button.textContent,'Renamed');
  assert.equal(row.children.length,1);
  button.onpointerup({pointerType:'touch'});
  button.onpointerup({pointerType:'touch'});
  input = row.children[1];
  assert.equal(input.tagName,'input');
  input.onkeydown({key:'Escape',preventDefault(){}});
  button.onkeydown({key:'F2',preventDefault(){}});
  input = row.children[1]; input.value = 'Saved on blur';
  await input.onblur();
  assert.equal(writes,2);
  assert.equal(button.textContent,'Saved on blur');

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

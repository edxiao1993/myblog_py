(() => {
  const el = id => document.getElementById('coding-' + id);
  const t = (s, values) => window.AppI18n.t(s, values);
  let directory = '', project = '', sid = '', busy = false, running = false, appending = false, calls = new Map(), group = null, summary = null;
  let thinkingBlocks = new Map(), activeCalls = new Set();
  const projectKey = 'coding-agent-project-' + (el('app').dataset.userId || 'default');
  marked.setOptions({gfm: true, breaks: true});
  const settings = () => ({model: el('model').value, thinking: el('thinking').value, prompt: 'pi'});
  async function api(path, data) {
    const response = await fetch('/coding/api/pi' + path, {method: data === undefined ? 'GET' : 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': el('app').dataset.csrf}, body: data === undefined ? undefined : JSON.stringify(data)});
    if (!response.ok) { const value = await response.json(); throw new Error(t(value.error || 'Request failed.')); }
    return response;
  }
  function report(error) { el('status').textContent = error.message; el('directory-status').textContent = error.message; }
  function run(fn) { return (...args) => Promise.resolve(fn(...args)).catch(report); }
  function setActivity(message = '') {
    el('activity').textContent = message ? t(message) : '';
    el('activity-spinner').hidden = !message;
  }
  function setBusy(value) {
    busy = value;
    if (!value) setActivity();
    else if (running && el('activity-spinner').hidden) setActivity('Agent is working…');
    document.querySelectorAll('#coding-app input, #coding-app select, #coding-app button, #coding-app textarea').forEach(node => { node.disabled = value && node.dataset.copy !== 'true' && node !== el('jump-latest'); });
    el('message').disabled = value && !running;
    el('send').disabled = (value && !running) || appending;
    el('stop').disabled = !running;
  }
  function copyButton(parent, text) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'btn btn-sm btn-outline-secondary coding-copy mt-2'; button.dataset.copy = 'true'; button.textContent = t('Copy');
    let copying = false;
    button.onclick = async event => {
      event.stopPropagation();
      if (copying) return;
      copying = true;
      try { await navigator.clipboard.writeText(text()); el('status').textContent = t('✓ Copied!'); }
      catch { el('status').textContent = t('Unable to copy. Please select and copy the text manually.'); }
      finally { copying = false; }
    };
    parent.append(button);
  }
  function collapsible(card) {
    const collapse = event => {
      if (!card.open || event.target.closest('details') !== card || event.target.closest('summary, button, a, input, textarea, select')) return;
      card.open = false; event.stopPropagation();
    };
    card.ondblclick = collapse;
    let lastTap = 0;
    card.onpointerup = event => {
      if (event.pointerType !== 'touch') return;
      const now = Date.now();
      if (lastTap && now - lastTap < 350) { lastTap = 0; collapse(event); }
      else lastTap = now;
    };
  }
  function textCard(label, text, details = false, parent = el('messages')) {
    const card = document.createElement(details ? 'details' : 'div'); card.className = 'border rounded p-2 mb-2';
    const title = document.createElement(details ? 'summary' : 'strong'); title.textContent = label;
    const body = document.createElement('pre'); body.textContent = text;
    card.append(title, body); copyButton(card, () => body.textContent);
    if (details) collapsible(card);
    parent.append(card); return body;
  }
  function updateMarkdown(body, text) {
    body.dataset.markdown = text;
    body.innerHTML = DOMPurify.sanitize(marked.parse(text));
  }
  function messageCard(role, text) {
    const card = document.createElement('div');
    card.className = 'border rounded p-2 mb-2' + (role === 'user' ? ' coding-user-message' : '');
    if (role !== 'user') {
      const title = document.createElement('strong'); title.textContent = t('Assistant'); card.append(title);
    }
    const body = document.createElement('div'); body.className = 'coding-markdown';
    updateMarkdown(body, text); card.append(body);
    if (role !== 'user') copyButton(card, () => body.dataset.markdown);
    el('messages').append(card);
    return body;
  }
  const content = message => typeof message.content === 'string' ? message.content : (message.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n');
  function refreshActivityGroup(force = false) {
    if (!group || (!force && calls.size <= 10 && !summary)) return;
    if (!summary) {
      const wrapper = document.createElement('details'); summary = document.createElement('summary');
      group.replaceWith(wrapper); wrapper.append(summary, group); collapsible(wrapper);
      wrapper.open = activeCalls.size > 0;
      const groupedCalls = calls;
      copyButton(wrapper, () => Array.from(groupedCalls.values(), body => body.parentElement.children[0].textContent + '\n' + body.textContent).join('\n\n'));
    }
    summary.textContent = t('Tool calls ({count})', {count: calls.size});
  }
  function collapseActivity() {
    for (const body of calls.values()) body.parentElement.open = false;
    activeCalls.clear();
    refreshActivityGroup(true);
    if (summary) summary.parentElement.open = false;
  }
  function tool(id, name, value, replace = false, active = false) {
    if (!group) { group = document.createElement('div'); el('messages').append(group); }
    if (!calls.has(id)) calls.set(id, textCard(name, '', true, group));
    const body = calls.get(id), wasActive = activeCalls.has(id);
    if (!replace && body.textContent && body.textContent !== value) body.textContent += '\n\n' + value;
    else body.textContent = value;
    if (active) {
      activeCalls.add(id);
      if (!wasActive) body.parentElement.open = true;
    } else {
      activeCalls.delete(id); body.parentElement.open = false;
    }
    refreshActivityGroup();
    if (summary && active && !wasActive) summary.parentElement.open = true;
    if (summary && !activeCalls.size && !active) summary.parentElement.open = false;
    return body;
  }
  function thinking(index, text, append = false, active = append) {
    if (!thinkingBlocks.has(index)) thinkingBlocks.set(index, {id: {}, text: ''});
    const block = thinkingBlocks.get(index);
    block.text = append ? block.text + text : text;
    tool(block.id, t('Thinking'), block.text, true, active);
  }
  function activityEvent(event) {
    const updateType = event.assistantMessageEvent?.type;
    if (event.type === 'tool_execution_start' || event.type === 'tool_execution_update') setActivity('Running a tool…');
    else if (event.type === 'tool_execution_end') setActivity('Agent is working…');
    else if (updateType === 'thinking_start' || updateType === 'thinking_delta') setActivity('Thinking…');
    else if (updateType === 'thinking_end') setActivity('Agent is working…');
    else if (updateType === 'text_delta') setActivity('Preparing a response…');
    else if (event.type === 'done' || event.type === 'error') setActivity();
    if (event.type === 'message_start' && event.message?.role === 'assistant') thinkingBlocks = new Map();
    const update = event.assistantMessageEvent;
    if (event.type === 'message_update') {
      if (update?.type === 'thinking_start') thinking(update.contentIndex, '', false, true);
      if (update?.type === 'thinking_delta') thinking(update.contentIndex, update.delta, true);
      if (update?.type === 'thinking_end') thinking(update.contentIndex,
        typeof update.content === 'string' ? update.content : thinkingBlocks.get(update.contentIndex)?.text || '', false, false);
      if (update?.type === 'text_delta' && update.delta) collapseActivity();
    }
    if (event.type === 'tool_execution_start') {
      const args = JSON.stringify(event.args || {}, null, 2);
      tool(event.toolCallId, event.toolName, args, true, true).dataset.arguments = args;
    }
    if (event.type === 'tool_execution_update' || event.type === 'tool_execution_end') {
      const args = calls.get(event.toolCallId)?.dataset.arguments || '';
      const output = (event.isError ? t('Failed') + '\n' : '') + content(event.result || event.partialResult || {});
      tool(event.toolCallId, event.toolName, args + (args && output ? '\n\n' : '') + output,
        true, event.type === 'tool_execution_update');
    }
    if (event.type === 'done' || event.type === 'error') collapseActivity();
  }
  function newTurn() { calls = new Map(); group = null; summary = null; thinkingBlocks = new Map(); activeCalls = new Set(); }
  function render(message, streamed = false) {
    if (message.role === 'assistant' && streamed !== true) thinkingBlocks = new Map();
    if (message.role === 'user') newTurn();
    if (message.role === 'toolResult') { tool(message.toolCallId, message.toolName, content(message)); return; }
    for (const [index, block] of (Array.isArray(message.content) ? message.content : []).entries()) {
      if (block.type === 'thinking') thinking(index, block.thinking || '');
      if (block.type === 'toolCall') tool(block.id, block.name, JSON.stringify(block.arguments, null, 2));
    }
    if (content(message)) {
      if (message.role === 'assistant') collapseActivity();
      messageCard(message.role, content(message));
    }
  }
  function updateActiveSession() {
    for (const row of el('history').children) {
      const btn = row.children[0];
      if (btn && btn.tagName && btn.tagName.toUpperCase() === 'BUTTON') {
        const active = Boolean(sid && btn.dataset.sessionId === sid);
        btn.setAttribute('aria-current', active ? 'true' : 'false');
      }
    }
  }
  function confirmDelete(title) {
    const dialog = el('delete-dialog');
    el('delete-description').textContent = t('Delete "{title}" from history? Pi transcript files will be preserved.', {title});
    return new Promise(resolve => {
      dialog.returnValue = '';
      dialog.onclose = () => { dialog.onclose = null; resolve(dialog.returnValue === 'delete'); };
      el('delete-cancel').onclick = () => dialog.close('cancel');
      el('delete-confirm').onclick = () => dialog.close('delete');
      dialog.showModal();
      el('delete-cancel').focus();
    });
  }
  async function history() {
    const sessions = await (await api('/sessions?project=' + encodeURIComponent(project))).json(); el('history').replaceChildren();
    for (const item of sessions.filter(item => item.project === project)) {
      const button = document.createElement('button'); button.className = 'coding-history-btn btn btn-sm flex-grow-1 text-start text-break coding-history-title'; button.style.minWidth = '0'; button.textContent = item.title;
      button.dataset.sessionId = item.id;
      if (sid && item.id === sid) button.setAttribute('aria-current', 'true');
      const remove = document.createElement('button'); remove.type = 'button';
      remove.className = 'btn btn-sm btn-outline-danger coding-history-delete';
      const icon = document.createElement('span'); icon.setAttribute('aria-hidden', 'true'); icon.textContent = '×'; remove.append(icon);
      remove.title = t('Delete');
      remove.setAttribute('aria-label', t('Delete conversation: {title}', {title: item.title}));
      const row = document.createElement('div'); row.className = 'coding-history-row d-flex mb-1'; row.append(button, remove); el('history').append(row);
      let clickTimer, editing = false, lastTap = 0;
      const open = run(async () => {
        if (busy || editing) return;
        setBusy(true);
        try {
          const detail = await (await api('/sessions/' + item.id + '/detail')).json();
          sid = item.id; updateActiveSession(); el('messages').replaceChildren(); newTurn();
          const active = detail.active_run;
          const current = active ? detail.messages.findIndex(message => message.id === active.user_message_id) : -1;
          (current < 0 ? detail.messages : detail.messages.slice(0, current)).forEach(render);
          if (active) {
            running = true; setBusy(true);
            await consumeStream(await api('/sessions/' + sid + '/runs/' + active.id + '/events'), true);
            running = false; await history();
          }
          await options(detail.session.settings.model, detail.session.settings.thinking);
        } finally { running = false; setBusy(false); }
      });
      remove.onclick = run(async () => {
        clearTimeout(clickTimer);
        if (busy || editing || el('delete-dialog').open) return;
        if (!await confirmDelete(item.title) || busy || editing) return;
        setBusy(true);
        try {
          await api('/sessions/' + item.id + '/delete', {});
          if (sid === item.id) { sid = ''; newTurn(); el('messages').replaceChildren(); }
          await history();
          el('status').textContent = t('Conversation deleted from history.');
          el('new').focus();
        } finally { setBusy(false); }
      });
      const rename = () => {
        clearTimeout(clickTimer);
        if (busy || editing) return;
        editing = true; button.hidden = true;
        const input = document.createElement('input'); input.className = 'form-control'; input.value = item.title; input.maxLength = 200;
        input.setAttribute('aria-label', t('Session name'));
        const restore = () => { editing = false; button.hidden = false; row.replaceChildren(button, remove); };
        let saving = false;
        const save = async () => {
          if (!editing || saving) return;
          const name = input.value.trim();
          if (name === item.title) { restore(); return; }
          saving = true; setBusy(true);
          try {
            const metadata = await (await api('/sessions/' + item.id + '/name', {name})).json();
            item.title = metadata.title; button.textContent = item.title;
            remove.setAttribute('aria-label', t('Delete conversation: {title}', {title: item.title})); restore();
          } catch (error) { report(error); }
          finally { saving = false; setBusy(false); if (editing) input.focus(); }
        };
        input.onkeydown = event => {
          if (event.isComposing) return;
          if (event.key === 'Escape' && !saving) { event.preventDefault(); restore(); button.focus(); }
          if (event.key === 'Enter') { event.preventDefault(); return save(); }
        };
        input.onblur = save;
        row.append(input); input.focus(); input.select();
      };
      button.title = t('Double-click to rename. Enter to save, Escape to cancel.');
      button.setAttribute('aria-keyshortcuts', 'F2');
      button.onclick = event => {
        clearTimeout(clickTimer);
        if (busy || editing) return;
        if (event.detail === 0 && !lastTap) return open();
        clickTimer = setTimeout(() => { if (button.isConnected) open(); }, 350);
      };
      button.ondblclick = rename;
      button.onkeydown = event => { if (event.key === 'F2') { event.preventDefault(); rename(); } };
      button.onpointerup = event => {
        if (event.pointerType !== 'touch') return;
        const now = Date.now();
        if (lastTap && now - lastTap < 350) { lastTap = 0; rename(); }
        else lastTap = now;
      };
    }
  }
  async function options(model = '', thinking = '') {
    const data = await (await api('/options?project=' + encodeURIComponent(project) + '&model=' + encodeURIComponent(model))).json();
    el('model').replaceChildren(new Option(t('Pi default'), ''));
    data.models.forEach(m => el('model').add(new Option(m.provider + '/' + m.id, m.provider + '/' + m.id))); el('model').value = model;
    el('thinking').replaceChildren(new Option(t('Pi default'), ''));
    data.levels.forEach(level => el('thinking').add(new Option(t(level), level))); el('thinking').value = thinking;
    el('command').replaceChildren(new Option(t('Choose command'), ''), new Option('/reload', 'reload'), new Option('/compact', 'compact'));
    data.commands.forEach(c => el('command').add(new Option('/' + c.name + (c.description ? ' — ' + c.description : ''), c.name)));
  }
  async function loadDirectory(path, name) {
    const data = await (await api('/projects' + (name === undefined ? '?path=' + encodeURIComponent(path) : ''), name === undefined ? undefined : {path, name})).json();
    directory = data.path; el('path').value = directory; el('directory-status').textContent = '';
    el('parent').disabled = !data.parent;
    el('parent').onclick = run(() => loadDirectory(data.parent || directory)); el('folders').replaceChildren();
    for (const folder of data.folders) {
      const button = document.createElement('button'); button.className = 'btn btn-link d-block text-break'; button.textContent = folder.name; button.onclick = run(() => loadDirectory(folder.path)); el('folders').append(button);
    }
  }
  async function selectProject() {
    if (directory === project) { await history(); return; }
    setBusy(true);
    try {
      project = directory; sid = '';
      try { localStorage.setItem(projectKey, project); } catch (_) { /* Storage can be unavailable. */ }
      el('project-name').textContent = project; el('project-name').title = project;
      el('messages').replaceChildren(); newTurn();
      await history(); await options();
    } finally { setBusy(false); }
  }
  async function browse(path) { await loadDirectory(path); await selectProject(); }
  async function consumeStream(response, showUser = false) {
    const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = '', streaming = null;
      while (true) {
        const {value, done} = await reader.read(); if (done) break; pending += decoder.decode(value, {stream: true});
        let index;
        while ((index = pending.indexOf('\n\n')) >= 0) {
          const frame = pending.slice(0, index); pending = pending.slice(index + 2);
          if (!frame.startsWith('data: ')) continue;
          const event = JSON.parse(frame.slice(6));
          if (event.type === 'api_user_message' && showUser) {
            newTurn(); messageCard('user', event.message);
          }
          activityEvent(event);
          if (event.type === 'message_update' && event.assistantMessageEvent?.type === 'text_delta') {
            if (!streaming) streaming = messageCard('assistant', '');
            updateMarkdown(streaming, streaming.dataset.markdown + event.assistantMessageEvent.delta);
          }
          if (event.type === 'message_end' && event.message.role === 'user') newTurn();
          if (event.type === 'message_end' && event.message.role !== 'user') {
            if (streaming) { streaming.parentElement.remove(); streaming = null; }
            render(event.message, true);
          }
          if (event.type === 'error') throw new Error(t(event.message));
          if (event.type === 'status') el('status').textContent = t(event.message);
          el('messages').scrollTop = el('messages').scrollHeight;
        }
      }
  }
  async function send(message) {
    if (appending || !project) return;
    if (busy) {
      if (!running) return;
      appending = true; setBusy(true);
      try {
        await api('/sessions/' + sid + '/append', {message});
        messageCard('user', message);
        if (el('message').value.trim() === message) el('message').value = '';
        el('status').textContent = t('Message queued for Pi.');
      } finally { appending = false; setBusy(busy); }
      return;
    }
    setBusy(true); el('status').textContent = ''; setActivity('Agent is working…');
    try {
      if (!sid) {
        sid = (await (await api('/sessions', {...settings(), project})).json()).id;
        await history(); setBusy(true);
      }
      newTurn(); messageCard('user', message);
      const response = await api('/sessions/' + sid + '/messages', {message, settings: settings()});
      running = true; setBusy(true);
      if (el('message').value.trim() === message) el('message').value = '';
      await consumeStream(response);
      running = false; setBusy(true); el('status').textContent = ''; await history();
    } finally { running = false; setBusy(false); }
  }
  el('directory').onclick = run(async () => { el('directory-dialog').showModal(); await loadDirectory(project); });
  el('directory-close').onclick = () => el('directory-dialog').close();
  el('select').onclick = run(async () => { await browse(el('path').value); el('directory-dialog').close(); });
  el('path').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); el('select').onclick(); } };
  el('browse').onclick = run(() => loadDirectory(el('path').value));
  el('create').onclick = run(() => loadDirectory(directory, el('folder-name').value));
  function updateJump() {
    const messages = el('messages');
    el('jump-latest').hidden = messages.scrollHeight - messages.clientHeight - messages.scrollTop <= 4;
  }
  el('jump-latest').onclick = () => {
    el('messages').scrollTop = el('messages').scrollHeight;
    updateJump();
  };
  el('messages').onscroll = updateJump;
  el('messages').addEventListener('load', updateJump, true);
  new MutationObserver(updateJump).observe(el('messages'), {childList: true, subtree: true, characterData: true, attributes: true});
  new ResizeObserver(updateJump).observe(el('messages'));
  updateJump();
  el('new').onclick = () => { sid = ''; updateActiveSession(); newTurn(); el('messages').replaceChildren(); };
  el('model').onchange = run(async () => { setBusy(true); try { await options(el('model').value, el('thinking').value); } finally { setBusy(false); } });
  el('form').onsubmit = run(async event => { event.preventDefault(); if (el('message').value.trim()) await send(el('message').value.trim()); });
  el('message').onkeydown = event => {
    if (event.key !== 'Enter' || !(event.metaKey || event.ctrlKey) || event.isComposing || event.repeat) return;
    event.preventDefault();
    if ((!busy || running) && !appending && el('message').value.trim()) el('form').requestSubmit();
  };
  el('command').onchange = run(async () => {
    const command = el('command').value;
    if (!command) return;
    el('command').value = '';
    if (command === 'reload') {
      setBusy(true); try { await options(el('model').value, el('thinking').value); el('status').textContent = t('Project commands and settings reloaded.'); } finally { setBusy(false); }
    } else await send('/' + command);
  });
  el('stop').onclick = run(async () => { await api('/sessions/' + sid + '/stop', {}); running = false; setBusy(busy); });
  async function initialize() {
    let saved = '';
    try { saved = localStorage.getItem(projectKey) || ''; } catch (_) { /* Storage can be unavailable. */ }
    try { await loadDirectory(saved); }
    catch (error) { if (!saved) throw error; await loadDirectory(''); }
    await selectProject();
  }
  initialize().catch(report);
})();

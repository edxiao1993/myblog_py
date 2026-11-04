(() => {
  const i18n = window.AppI18n || {t: (msg, vals = {}) => msg.replace(/\{(\w+)\}/g, (m, k) => vals[k] ?? m), locale: 'en'};
  const t = (msg, vals) => i18n.t(msg, vals);
  const locale = i18n.locale;
  const el = id => document.getElementById(`pi-${id}`);
  const csrf = el('app').dataset.csrf;
  const presets = JSON.parse(document.getElementById('pi-presets').textContent);
  const tools = new Map();
  const thoughts = new Map();
  let sid = '', busy = false, streaming = false, appending = false, started = false, assistant = null;
  let sessions = [], attachments = [], chosenFiles = [], promptSnapshot = null;
  let optionModels = [], optionLevels = [];

  if (typeof marked !== 'undefined') {
    marked.setOptions({
      gfm: true,
      breaks: true
    });
  }

  if (typeof DOMPurify !== 'undefined') {
    DOMPurify.addHook('afterSanitizeAttributes', node => {
      if (node.tagName === 'A' && !node.hasAttribute('download')) {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
    });
  }

  async function api(path, data) {
    const headers = {'X-CSRF-Token': csrf};
    const multipart = data instanceof FormData;
    if (data !== undefined && !multipart) headers['Content-Type'] = 'application/json';
    const response = await fetch(`/api/pi${path}`, data === undefined ? {} : {
      method: 'POST', headers, body: multipart ? data : JSON.stringify(data)
    });
    if (!response.ok || response.redirected) {
      const body = await response.json().catch(() => ({}));
      if (response.status === 413) {
        throw new Error(body.error || t('Upload failed (413). The server or proxy rejected the upload as too large. Application limit: {limit} MB per request.',
          {limit: el('app').dataset.uploadLimit || '200'}));
      }
      throw new Error(body.error || t('Request failed ({status}). Check your login and try again.', {status: response.status}));
    }
    return response;
  }

  const status = text => {
    el('status').textContent = t(text);
    const dot = document.getElementById('pi-status-indicator');
    if (dot) {
      dot.className = 'pi-pulse-dot';
      if (/working|running|loading|connecting|stopping|处理中|运行|加载|连接|停止/i.test(text)) {
        dot.classList.add('pi-dot-busy');
      } else if (/failed|error|ended before|失败|错误|中断/i.test(text)) {
        dot.classList.add('pi-dot-error');
      } else {
        dot.classList.add('pi-dot-ready');
      }
    }
  };

  function closeMobileDrawer() {
    const drawer = document.getElementById('pi-sidebar-drawer');
    if (drawer && window.innerWidth < 768 && typeof bootstrap !== 'undefined' && bootstrap.Offcanvas) {
      const inst = bootstrap.Offcanvas.getInstance(drawer);
      if (inst) inst.hide();
    }
  }

  function updateBadges() {
    const modelBadge = document.getElementById('pi-badge-model');
    const thinkingBadge = document.getElementById('pi-badge-thinking');
    if (modelBadge) {
      const opt = el('model').selectedOptions[0];
      const modelVal = opt?.textContent || el('model').value;
      modelBadge.textContent = '🤖 ' + (modelVal || t('Default'));
    }
    if (thinkingBadge) {
      const val = el('thinking').value;
      thinkingBadge.textContent = '🧠 ' + (val ? t('Thinking: {level}', {level: t(val)}) : t('Thinking: Default'));
    }
  }

  function autoExpandInput() {
    const input = el('input');
    input.style.height = 'auto';
    const newHeight = Math.min(Math.max(input.scrollHeight, 46), 160);
    input.style.height = `${newHeight}px`;
  }

  function setBusy(value, activeStream = false) {
    busy = value;
    streaming = value && activeStream;
    ['new', 'archive', 'model', 'upload', 'search', 'save-prompt', 'custom-prompt-name', 'custom-prompt-text'].forEach(id => { el(id).disabled = value; });
    el('input').disabled = value && !streaming;
    el('send').disabled = appending || (value && !streaming);
    el('thinking').disabled = value || optionLevels.length < 2;
    el('prompt').disabled = value || started;
    el('stop').disabled = !(value && streaming && sid);
    document.querySelectorAll('#pi-history button, #pi-files button, #pi-attachments button').forEach(button => { button.disabled = value; });
    el('messages').setAttribute('aria-busy', String(value));
  }

  const selectedSettings = () => ({model: el('model').value, thinking: el('thinking').value, prompt: el('prompt').value});
  const shortName = name => name.replace(/^[0-9a-f]{12}-/, '');

  function fileURL(path) {
    return `/api/pi/sessions/${sid}/files/${path.split('/').map(encodeURIComponent).join('/')}`;
  }

  function fileLink(path) {
    const link = document.createElement('a');
    const filename = shortName(path.split('/').pop());
    link.href = fileURL(path); link.textContent = filename; link.setAttribute('download', filename);
    link.onclick = async event => {
      event.preventDefault();
      if (link.getAttribute('aria-busy') === 'true') return;
      link.setAttribute('aria-busy', 'true');
      status(t('Downloading {filename}…', {filename}));
      try {
        const response = await api(`/sessions/${sid}/files/${path.split('/').map(encodeURIComponent).join('/')}`);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const save = document.createElement('a');
        save.href = url; save.download = filename;
        document.body.append(save); save.click(); save.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        status(t('Download ready: {filename}', {filename}));
      } catch (error) {
        const hint = error instanceof TypeError ? t(" Check that the server's HTTPS certificate is trusted by your browser.") : '';
        status(t('Download failed: {error}{hint}', {error: t(error.message), hint}));
      } finally {
        link.removeAttribute('aria-busy');
      }
    };
    return link;
  }

  function promptPreview() {
    const preset = presets[el('prompt').value];
    el('prompt-text').textContent = (promptSnapshot ?? preset?.text) || t('Use Pi’s native system prompt.');
    el('prompt-note').textContent = started
      ? t('This conversation keeps its saved prompt. Start a new conversation to choose another preset.')
      : t('Appended to Pi’s native instructions. Choose the preset before the first message. Pi also receives the conversation file paths.');
  }

  function renderHistory() {
    const filter = el('search').value.toLowerCase();
    el('history').replaceChildren();
    const visible = sessions.filter(item => item.title.toLowerCase().includes(filter));
    if (!visible.length) {
      const emptyDiv = document.createElement('div');
      emptyDiv.className = 'text-center text-muted py-4 small';
      emptyDiv.innerHTML = `<div class="fs-4 mb-1">💬</div><div>${sessions.length ? t('No matching conversations.') : t('No conversations yet.')}</div>`;
      el('history').append(emptyDiv);
      return;
    }
    visible.forEach(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn pi-history-btn text-start w-100 p-2 mb-1 d-flex flex-column border';
      button.setAttribute('aria-current', String(item.id === sid));
      button.disabled = busy;

      const title = document.createElement('div');
      title.className = 'text-truncate fw-semibold w-100 small';
      title.textContent = item.title;

      const date = document.createElement('small');
      date.className = 'text-muted';
      date.style.fontSize = '0.75rem';
      const ts = (item.updated || item.created) * 1000;
      date.textContent = new Date(ts).toLocaleString(locale, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });

      button.append(title, date);
      button.onclick = () => {
        openConversation(item.id);
        closeMobileDrawer();
      };
      el('history').append(button);
    });
  }

  async function refreshHistory() {
    sessions = await (await api('/sessions')).json();
    renderHistory();
  }

  function locationForConversation() {
    const url = new URL(location.href);
    if (sid) url.searchParams.set('conversation', sid); else url.searchParams.delete('conversation');
    history.replaceState(null, '', url);
  }

  function resetMessages() {
    assistant = null;
    tools.clear();
    thoughts.clear();
    el('messages').replaceChildren();
  }

  function render(body, text) {
    body.dataset.text = text;
    body.innerHTML = DOMPurify.sanitize(marked.parse(text));

    body.querySelectorAll('pre').forEach(pre => {
      if (pre.querySelector('.copy-code-btn')) return;
      const copyBtn = document.createElement('button');
      copyBtn.type = 'button';
      copyBtn.className = 'copy-code-btn';
      copyBtn.textContent = t('Copy');
      copyBtn.onclick = () => {
        const code = pre.querySelector('code')?.innerText || pre.innerText;
        navigator.clipboard.writeText(code).then(() => {
          copyBtn.textContent = t('✓ Copied!');
          copyBtn.classList.add('copied');
          setTimeout(() => {
            copyBtn.textContent = t('Copy');
            copyBtn.classList.remove('copied');
          }, 2000);
        }).catch(() => {
          copyBtn.textContent = t('Error');
        });
      };
      pre.style.position = 'relative';
      pre.appendChild(copyBtn);
    });

    body.querySelectorAll('table').forEach(tbl => {
      if (!tbl.parentElement.classList.contains('table-responsive')) {
        const wrapper = document.createElement('div');
        wrapper.className = 'table-responsive my-2';
        tbl.parentNode.insertBefore(wrapper, tbl);
        wrapper.appendChild(tbl);
        tbl.classList.add('table', 'table-sm', 'table-bordered', 'table-hover', 'mb-0');
      }
    });

    el('messages').scrollTop = el('messages').scrollHeight;
  }

  function bubble(role, text, attached = []) {
    const isUser = role === 'You';
    const section = document.createElement('section');
    section.className = `pi-msg-row d-flex ${isUser ? 'justify-content-end' : 'justify-content-start'} mb-3`;

    const wrap = document.createElement('div');
    wrap.className = `pi-bubble-wrap ${isUser ? 'text-end' : 'text-start'}`;

    const meta = document.createElement('div');
    meta.className = `small text-muted mb-1 ${isUser ? 'pe-1' : 'ps-1 d-flex align-items-center gap-2'}`;
    if (isUser) {
      meta.innerHTML = `<span class="fw-semibold">${t('You')}</span> <span class="pi-avatar">👤</span>`;
    } else {
      meta.innerHTML = `<span class="pi-avatar-ai">🤖</span> <span class="fw-bold text-dark">${t('Pi Agent')}</span>`;
    }

    const bubbleCard = document.createElement('div');
    bubbleCard.className = `pi-bubble ${isUser ? 'pi-user-bubble' : 'pi-assistant-bubble'} text-start shadow-sm`;

    const body = document.createElement('div');
    body.className = 'pi-msg-body markdown-body';
    bubbleCard.append(body);
    wrap.append(meta, bubbleCard);

    if (attached && attached.length) {
      const attachBox = document.createElement('div');
      attachBox.className = `pi-msg-attachments d-flex flex-wrap gap-1 mt-2 ${isUser ? 'justify-content-end' : 'justify-content-start'}`;
      attached.forEach(path => {
        const pill = document.createElement('span');
        pill.className = 'badge bg-light text-primary border d-inline-flex align-items-center gap-1 py-1 px-2';
        pill.textContent = '📎 ';
        pill.append(fileLink(path));
        attachBox.append(pill);
      });
      wrap.append(attachBox);
    }

    section.append(wrap);
    el('messages').append(section);
    render(body, text);
    return body;
  }

  function content(message) {
    if (typeof message.content === 'string') return message.content;
    return (message.content || []).filter(block => block.type === 'text').map(block => block.text).join('\n');
  }

  function activityCard(label, icon) {
    const wrapper = document.createElement('div');
    wrapper.className = 'pi-tool-card card border mb-2 shadow-xs';
    const details = document.createElement('details');
    details.className = 'pi-tool-details';
    details.open = false;
    const summary = document.createElement('summary');
    summary.className = 'pi-tool-summary card-header py-2 px-3 small user-select-none text-break';
    // Tool names and thinking are untrusted agent output.
    summary.textContent = `${icon} ${label}`;
    const cardBody = document.createElement('div');
    cardBody.className = 'card-body p-2 bg-light border-top';
    const body = document.createElement('pre');
    body.className = 'tool-output mb-0 small text-wrap';
    cardBody.append(body);
    details.append(summary, cardBody);
    wrapper.append(details);
    el('messages').append(wrapper);
    return body;
  }

  function tool(id, name, text) {
    let body = tools.get(id);
    if (!body) {
      body = activityCard(name || t('command'), '⚙️');
      tools.set(id, body);
    }
    body.textContent = text;
    el('messages').scrollTop = el('messages').scrollHeight;
  }

  function thinking(index, text, append = false) {
    let body = thoughts.get(index);
    if (!body) {
      body = activityCard(t('Thinking'), '🧠');
      thoughts.set(index, body);
    }
    body.textContent = (append ? body.textContent : '') + text;
    el('messages').scrollTop = el('messages').scrollHeight;
  }

  function assistantDetails(message) {
    for (const [index, block] of (Array.isArray(message.content) ? message.content : []).entries()) {
      if (block.type === 'thinking' && block.thinking) thinking(index, block.thinking);
      if (block.type === 'toolCall') tool(block.id, block.name, JSON.stringify(block.arguments, null, 2));
    }
  }

  function renderAttachments() {
    el('attachments').replaceChildren();
    const entries = [
      ...attachments.map(path => ({name: shortName(path.split('/').pop()), path})),
      ...chosenFiles.map((file, index) => ({name: file.name, index}))
    ];
    entries.forEach(item => {
      const pill = document.createElement('span');
      pill.className = 'badge bg-primary-subtle text-primary border border-primary-subtle d-inline-flex align-items-center gap-1 py-1 px-2';
      const nameSpan = document.createElement('span');
      nameSpan.textContent = '📎 ' + item.name;
      nameSpan.className = 'text-truncate';
      nameSpan.style.maxWidth = '180px';
      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'btn-close btn-close-sm ms-1';
      removeBtn.style.fontSize = '0.65rem';
      removeBtn.title = t('Remove attachment');
      removeBtn.disabled = busy;
      removeBtn.onclick = () => {
        if (item.path) attachments = attachments.filter(path => path !== item.path);
        else chosenFiles.splice(item.index, 1);
        renderAttachments();
      };
      pill.append(nameSpan, removeBtn);
      el('attachments').append(pill);
    });
  }

  function renderFiles(files) {
    el('files').replaceChildren();
    const countEl = document.getElementById('pi-files-count');
    if (countEl) countEl.textContent = String(files.length);
    if (!files.length) {
      el('files').innerHTML = `<div class="text-muted text-center py-3">${t('No files uploaded or generated yet.')}</div>`;
      return;
    }
    files.forEach(file => {
      const isOutput = file.path.startsWith('outputs/');
      const card = document.createElement('div');
      card.className = 'd-flex flex-wrap justify-content-between align-items-center gap-2 border-bottom py-2';

      const left = document.createElement('div');
      left.className = 'd-flex align-items-center gap-2 min-width-0';
      const badge = document.createElement('span');
      badge.className = `badge ${isOutput ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-secondary-subtle text-secondary border border-secondary-subtle'}`;
      badge.textContent = isOutput ? t('Output') : t('Upload');

      const link = fileLink(file.path);
      link.className = 'fw-medium text-break text-decoration-none';
      const size = document.createElement('small');
      size.className = 'text-muted';
      size.textContent = `(${Math.ceil(file.size / 1024)} KB)`;

      left.append(badge, link, size);

      const actions = document.createElement('div');
      actions.className = 'd-flex align-items-center gap-1 flex-shrink-0';

      if (file.path.startsWith('uploads/')) {
        const attachBtn = document.createElement('button');
        attachBtn.type = 'button';
        attachBtn.className = 'btn btn-sm btn-outline-primary py-0 px-2';
        attachBtn.style.fontSize = '0.78rem';
        attachBtn.textContent = t('+ Attach');
        attachBtn.disabled = busy;
        attachBtn.onclick = () => {
          if (attachments.length + chosenFiles.length >= 20) { status('Attach up to 20 files.'); return; }
          if (!attachments.includes(file.path)) attachments.push(file.path);
          renderAttachments();
        };
        actions.append(attachBtn);
      }
      card.append(left, actions);
      el('files').append(card);
    });
  }

  async function refreshFiles() {
    if (sid) renderFiles(await (await api(`/sessions/${sid}/files`)).json());
  }

  async function loadOptions(model = '', thinking = '') {
    const options = await (await api(`/options?model=${encodeURIComponent(model)}`)).json();
    optionModels = options.models; optionLevels = options.levels;
    el('model').replaceChildren(new Option(t('Pi default') + (!model && options.current_model ? ` (${options.current_model})` : ''), ''));
    const groups = new Map();
    optionModels.forEach(item => {
      if (!groups.has(item.provider)) {
        const group = document.createElement('optgroup'); group.label = item.provider; groups.set(item.provider, group); el('model').append(group);
      }
      groups.get(item.provider).append(new Option(item.name || item.id, `${item.provider}/${item.id}`));
    });
    if (model && !optionModels.some(item => `${item.provider}/${item.id}` === model)) el('model').add(new Option(model, model));
    el('model').value = model;
    el('thinking').replaceChildren(new Option(t('Pi default') + (options.current_thinking ? ` (${t(options.current_thinking)})` : ''), ''));
    optionLevels.forEach(level => el('thinking').add(new Option(t(level), level)));
    el('thinking').value = optionLevels.includes(thinking) ? thinking : '';
    updateBadges();
  }

  async function openConversation(id) {
    if (busy) return;
    setBusy(true); status('Loading conversation…');
    try {
      const detail = await (await api(`/sessions/${id}/detail`)).json();
      sid = id; started = detail.messages.length > 0; attachments = []; chosenFiles = []; el('upload').value = ''; el('input').value = '';
      autoExpandInput();
      resetMessages(); renderAttachments();
      el('title').textContent = detail.session.title;
      const settings = detail.session.settings;
      if (!Array.from(el('model').options).some(option => option.value === settings.model)) el('model').add(new Option(settings.model, settings.model));
      el('model').value = settings.model;
      if (!Array.from(el('thinking').options).some(option => option.value === settings.thinking)) el('thinking').add(new Option(settings.thinking, settings.thinking));
      el('thinking').value = settings.thinking;
      el('prompt').value = settings.prompt; promptSnapshot = detail.session.prompt_text; promptPreview();
      for (const message of detail.messages) {
        if (message.role === 'toolResult') tool(message.toolCallId, message.toolName, content(message));
        else if (['user', 'assistant'].includes(message.role)) {
          if (content(message)) bubble(message.role === 'user' ? 'You' : 'Assistant', content(message), message.attachments);
          if (message.role === 'assistant') { thoughts.clear(); assistantDetails(message); }
        }
      }
      renderFiles(detail.files); locationForConversation(); renderHistory();
      await loadOptions(settings.model, settings.thinking);
      updateBadges();
      status('Conversation restored. Send a message to continue with Pi’s saved context.');
      el('input').placeholder = t('Continue this conversation…');
    } catch (error) { status(error.message); }
    finally { setBusy(false); }
  }

  function event(record) {
    if (record.type === 'message_start' && record.message?.role === 'assistant') { assistant = null; thoughts.clear(); }
    if (record.type === 'message_update' && record.assistantMessageEvent?.type === 'text_delta') {
      assistant ||= bubble('Assistant', ''); render(assistant, assistant.dataset.text + record.assistantMessageEvent.delta);
    }
    if (record.type === 'message_update') {
      const update = record.assistantMessageEvent;
      if (update?.type === 'thinking_delta') thinking(update.contentIndex, update.delta, true);
      if (update?.type === 'thinking_end') thinking(update.contentIndex, update.content);
    }
    if (record.type === 'message_end' && record.message.role === 'assistant') {
      assistantDetails(record.message);
      const text = content(record.message);
      if (text) { assistant ||= bubble('Assistant', ''); render(assistant, text); }
      if (record.message.errorMessage) throw new Error(record.message.errorMessage);
      assistant = null;
    }
    if (record.type === 'tool_execution_start') {
      assistant = null; tool(record.toolCallId, record.toolName, JSON.stringify(record.args, null, 2)); status(t('Running {tool}…', {tool: record.toolName}));
    }
    if (['tool_execution_update', 'tool_execution_end'].includes(record.type)) {
      const result = record.result || record.partialResult;
      tool(record.toolCallId, record.toolName, `${record.isError ? t('Failed') + '\n' : ''}${content(result || {}) || JSON.stringify(result, null, 2)}`);
    }
    if (record.type === 'status') status(record.message);
    if (record.type === 'error') throw new Error(record.message);
    if (record.type === 'done') status('Ready');
  }

  el('search').oninput = renderHistory;
  el('prompt').onchange = () => { promptSnapshot = null; promptPreview(); };
  el('custom-prompt-form').onsubmit = async e => {
    e.preventDefault();
    if (busy) return;
    const name = el('custom-prompt-name').value.trim();
    const text = el('custom-prompt-text').value.trim();
    setBusy(true);
    try {
      const preset = await (await api('/prompts', {name, text})).json();
      presets[preset.id] = preset;
      el('prompt').add(new Option(preset.name, preset.id));
      if (!started) { el('prompt').value = preset.id; promptSnapshot = null; promptPreview(); }
      el('custom-prompt-form').reset();
      el('custom-prompt-status').textContent = t(started
        ? 'Prompt saved. Start a new conversation to use it.' : 'Prompt saved and selected.');
    } catch (error) { el('custom-prompt-status').textContent = error.message; }
    finally { setBusy(false); }
  };
  el('thinking').onchange = () => {
    updateBadges();
    status('Settings will apply to your next message.');
  };
  el('model').onchange = async () => {
    const model = el('model').value, thinking = el('thinking').value;
    setBusy(true); status('Loading supported thinking levels…');
    try {
      await loadOptions(model, thinking);
      updateBadges();
      status('Settings will apply to your next message.');
    } catch (error) { status(error.message); }
    finally { setBusy(false); }
  };
  el('upload').onchange = () => {
    const files = Array.from(el('upload').files);
    if (files.length + attachments.length > 20) { el('upload').value = ''; status('Attach up to 20 files.'); return; }
    chosenFiles = files; renderAttachments();
  };
  el('new').onclick = async () => {
    if (busy) return;
    sid = ''; started = false; promptSnapshot = null; attachments = []; chosenFiles = [];
    resetMessages(); renderAttachments(); renderFiles([]); el('upload').value = ''; el('input').value = '';
    autoExpandInput();
    el('title').textContent = t('New conversation'); el('input').placeholder = t('Ask Pi a question or describe a task…');
    el('prompt').value = 'general'; locationForConversation(); renderHistory(); promptPreview(); setBusy(true);
    closeMobileDrawer();
    try { await loadOptions(); updateBadges(); status('Ready'); } catch (error) { status(error.message); }
    finally { setBusy(false); el('input').focus(); }
  };
  el('archive').onclick = async () => {
    if (!sid || !started) { status('Send a message first.'); return; }
    setBusy(true);
    try {
      const result = await (await api(`/sessions/${sid}/archive`, {})).json();
      const link = document.createElement('a');
      link.href = result.url;
      link.className = 'btn btn-sm btn-outline-success py-0 px-2 ms-1';
      link.textContent = t('View saved archive →');
      el('status').replaceChildren(document.createTextNode(t('Snapshot saved! ')), link);
    } catch (error) { status(error.message); }
    finally { setBusy(false); }
  };
  el('stop').onclick = async () => {
    el('stop').disabled = true;
    try { await api(`/sessions/${sid}/stop`, {}); status('Stopping…'); }
    catch (error) { status(error.message); el('stop').disabled = false; }
  };

  el('input').addEventListener('input', autoExpandInput);
  el('input').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !e.isComposing) {
      e.preventDefault();
      el('form').dispatchEvent(new Event('submit', {cancelable: true}));
    }
  });

  el('form').onsubmit = async e => {
    e.preventDefault();
    const text = el('input').value.trim();
    if (streaming) {
      if (!text || appending) return;
      appending = true; el('send').disabled = true;
      try {
        await api(`/sessions/${sid}/append`, {message: text});
        bubble('You', text);
        if (el('input').value.trim() === text) { el('input').value = ''; autoExpandInput(); }
        status('Message queued for Pi.');
      } catch (error) { status(error.message); }
      finally { appending = false; el('send').disabled = busy && !streaming; }
      return;
    }
    if (busy || (!text && !attachments.length && !chosenFiles.length)) return;
    const message = text || t('Please review the attached files.');
    setBusy(true); status('Connecting…'); assistant = null;
    let reader;
    try {
      if (!sid) {
        sid = (await (await api('/sessions', selectedSettings())).json()).id;
        locationForConversation();
      }
      if (chosenFiles.length) {
        const form = new FormData(); chosenFiles.forEach(file => form.append('files', file));
        const uploaded = await (await api(`/sessions/${sid}/files`, form)).json();
        attachments.push(...uploaded.files.map(file => file.path)); chosenFiles = []; el('upload').value = ''; renderAttachments();
      }
      const response = await api(`/sessions/${sid}/messages`, {message, settings: selectedSettings(), attachments});
      bubble('You', message, attachments); started = true; promptPreview();
      attachments = []; renderAttachments(); el('input').value = ''; autoExpandInput(); setBusy(true, true);
      reader = response.body.getReader();
      const decoder = new TextDecoder(); let pending = '', doneEvent = false;
      status('Working…');
      while (true) {
        const {value, done} = await reader.read(); pending += decoder.decode(value, {stream: !done});
        let boundary;
        while ((boundary = pending.indexOf('\n\n')) >= 0) {
          const frame = pending.slice(0, boundary); pending = pending.slice(boundary + 2);
          if (frame.startsWith('data: ')) {
            const record = JSON.parse(frame.slice(6)); if (record.type === 'done') doneEvent = true; event(record);
          }
        }
        if (done) break;
      }
      if (!doneEvent) throw new Error(t('Connection ended before completion. Reopen the conversation to recover saved messages.'));
    } catch (error) { status(error.message); if (reader) await reader.cancel().catch(() => {}); }
    finally {
      try {
        await refreshHistory(); await refreshFiles();
        el('title').textContent = sessions.find(item => item.id === sid)?.title || t('New conversation');
      } catch (error) { status(error.message); }
      setBusy(false); el('input').focus();
    }
  };

  function setToolsVisibility(show) {
    if (show) {
      el('app').classList.remove('hide-tools');
    } else {
      el('app').classList.add('hide-tools');
    }
    const t1 = document.getElementById('pi-toggle-tools');
    const t2 = document.getElementById('pi-settings-toggle-tools');
    if (t1) t1.checked = show;
    if (t2) t2.checked = show;
  }

  const toggleTools = document.getElementById('pi-toggle-tools');
  if (toggleTools) {
    toggleTools.onchange = e => setToolsVisibility(e.target.checked);
  }
  const settingsToggleTools = document.getElementById('pi-settings-toggle-tools');
  if (settingsToggleTools) {
    settingsToggleTools.onchange = e => setToolsVisibility(e.target.checked);
  }

  function setDesktopSidebarHidden(hidden) {
    if (hidden) {
      el('app').classList.add('pi-desktop-sidebar-hidden');
    } else {
      el('app').classList.remove('pi-desktop-sidebar-hidden');
    }
    try { localStorage.setItem('pi_sidebar_desktop_hidden', String(hidden)); } catch (_) {}
  }

  const sidebarToggle = document.getElementById('pi-sidebar-toggle');
  if (sidebarToggle) {
    sidebarToggle.onclick = () => {
      if (window.innerWidth < 768) {
        const drawer = document.getElementById('pi-sidebar-drawer');
        if (drawer && typeof bootstrap !== 'undefined' && bootstrap.Offcanvas) {
          const inst = bootstrap.Offcanvas.getOrCreateInstance(drawer);
          inst.toggle();
        }
      } else {
        const isHidden = el('app').classList.contains('pi-desktop-sidebar-hidden');
        setDesktopSidebarHidden(!isHidden);
      }
    };
  }

  const hideDesktopBtn = document.getElementById('pi-sidebar-hide-desktop');
  if (hideDesktopBtn) {
    hideDesktopBtn.onclick = () => setDesktopSidebarHidden(true);
  }

  const showDesktopBtn = document.getElementById('pi-sidebar-show-desktop');
  if (showDesktopBtn) {
    showDesktopBtn.onclick = () => setDesktopSidebarHidden(false);
  }

  async function initialize() {
    setToolsVisibility(false);
    try {
      setDesktopSidebarHidden(localStorage.getItem('pi_sidebar_desktop_hidden') !== 'false');
    } catch (_) {}

    autoExpandInput();
    setBusy(true);
    try { await refreshHistory(); } catch (error) { status(error.message); }
    const requested = new URL(location.href).searchParams.get('conversation');
    setBusy(false);
    if (requested) { await openConversation(requested); return; }
    setBusy(true);
    try { await loadOptions(); updateBadges(); status('Ready'); }
    catch (error) { status(t('{error} You can still use Pi’s default settings.', {error: t(error.message)})); }
    finally { setBusy(false); promptPreview(); }
  }

  initialize();
})();

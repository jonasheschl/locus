<script>
  import { afterUpdate, onMount } from 'svelte';
  import {
    ArrowUp,
    BookOpenText,
    Bot,
    Check,
    ChevronRight,
    FileInput,
    FilePlus2,
    KeyRound,
    Link2,
    LoaderCircle,
    MessageSquarePlus,
    Paperclip,
    Plus,
    RotateCcw,
    Trash2,
    UserRound,
    X
  } from '@lucide/svelte';
  import Markdown from './Markdown.svelte';
  import AgentActivity from './AgentActivity.svelte';
  import { api, getThread, getThreads } from './api.js';

  export let authStatus = { authenticated: false };
  export let files = [];
  export let initialPrompt = '';
  export let initialContext = [];
  export let onLogin = () => {};
  export let onFilesChanged = () => {};
  export let onOpenSource = () => {};

  let input = '';
  let messages = [];
  let threads = [];
  let threadId = null;
  let contextPaths = [...initialContext];
  let streaming = false;
  let statusMessage = '';
  let scrollArea;
  let fileInput;
  let addMenuOpen = false;
  let uploadError = '';
  let url = '';
  let addingUrl = false;
  let lastPrompt = '';
  let undoing = '';
  let threadContextMenu = null;
  let threadError = '';
  let followOutput = true;

  $: unprocessed = files.filter((file) => file.space === 'ingest' && file.integration_status !== 'integrated').slice(0, 8);
  $: if (initialPrompt && initialPrompt !== lastPrompt) {
    lastPrompt = initialPrompt;
    input = initialPrompt;
    contextPaths = [...initialContext];
  }

  onMount(loadThreads);
  afterUpdate(() => {
    if (scrollArea && followOutput) scrollArea.scrollTop = scrollArea.scrollHeight;
  });

  async function loadThreads() {
    try { ({ threads } = await getThreads()); } catch (_) { threads = []; }
  }

  async function openThread(id) {
    if (streaming) return;
    followOutput = true;
    const result = await getThread(id);
    threadId = id;
    messages = [
      ...result.messages,
      ...(result.operations || []).map((operation) => ({
        role: 'operation', operation, created_at: operation.completed_at || operation.created_at
      }))
    ].sort((left, right) => new Date(left.created_at) - new Date(right.created_at));
    input = '';
    contextPaths = [];
  }

  function newChat() {
    if (streaming) return;
    followOutput = true;
    messages = [];
    threadId = null;
    input = '';
    contextPaths = [];
    statusMessage = '';
  }

  function showThreadContextMenu(event, thread) {
    event.preventDefault();
    event.stopPropagation();
    threadContextMenu = {
      thread,
      x: Math.min(event.clientX, window.innerWidth - 220),
      y: Math.min(event.clientY, window.innerHeight - 70)
    };
  }

  async function deleteThread(thread) {
    threadContextMenu = null;
    if (streaming) return;
    if (!window.confirm(`Delete “${thread.title}”? This removes the conversation history.`)) return;
    threadError = '';
    try {
      await api(`/api/chat/threads/${encodeURIComponent(thread.id)}`, { method: 'DELETE' });
      threads = threads.filter((candidate) => candidate.id !== thread.id);
      if (threadId === thread.id) newChat();
    } catch (cause) {
      threadError = cause.message;
    }
  }

  async function ask(prompt = input) {
    const question = prompt.trim();
    if (!question || streaming) return;
    if (!authStatus.authenticated) { onLogin(); return; }
    input = '';
    followOutput = true;
    messages = [...messages, { role: 'user', content: question }, { role: 'assistant', content: '', activities: [] }];
    streaming = true;
    statusMessage = 'Reading the Wiki and deciding what the conversation needs…';
    try {
      const response = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, thread_id: threadId, context_paths: contextPaths })
      });
      if (!response.ok || !response.body) throw new Error('Could not start the Codex request.');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) if (line.trim()) handleEvent(JSON.parse(line));
        if (done) break;
      }
      if (buffer.trim()) handleEvent(JSON.parse(buffer));
    } catch (cause) {
      failRunningActivities();
      updateAssistant(`I hit a problem: ${cause.message}`);
    } finally {
      streaming = false;
      statusMessage = '';
      loadThreads();
      await onFilesChanged();
    }
  }

  function handleEvent(event) {
    if (event.type === 'thread') threadId = event.thread.id;
    if (event.type === 'status') statusMessage = event.message;
    if (event.type === 'activity') updateActivity(event.activity);
    if (event.type === 'delta') appendAssistant(event.delta);
    if (event.type === 'done') {
      threadId = event.thread_id;
      if (!messages[messages.length - 1]?.content) updateAssistant(event.content);
    }
    if (event.type === 'operation') messages = [...messages, { role: 'operation', operation: event.operation }];
    if (event.type === 'error') {
      failRunningActivities();
      updateAssistant(`I couldn't complete that request: ${event.message}`);
    }
  }

  function updateActivity(activity) {
    const assistantIndex = messages.findLastIndex((message) => message.role === 'assistant');
    if (assistantIndex < 0) return;
    const assistant = messages[assistantIndex];
    const activities = [...(assistant.activities || [])];
    const activityIndex = activities.findIndex((candidate) => candidate.id === activity.id);
    if (activityIndex >= 0) activities[activityIndex] = { ...activities[activityIndex], ...activity };
    else activities.push(activity);
    messages = messages.map((message, index) => index === assistantIndex ? { ...message, activities } : message);
  }

  function failRunningActivities() {
    const assistantIndex = messages.findLastIndex((message) => message.role === 'assistant');
    if (assistantIndex < 0) return;
    const assistant = messages[assistantIndex];
    const activities = (assistant.activities || []).map((activity) =>
      activity.status === 'running' ? { ...activity, status: 'failed' } : activity
    );
    messages = messages.map((message, index) => index === assistantIndex ? { ...message, activities } : message);
  }

  function appendAssistant(delta) {
    const last = messages[messages.length - 1] || { role: 'assistant', content: '' };
    messages = [...messages.slice(0, -1), { ...last, content: last.content + delta }];
  }

  function updateAssistant(content) {
    const last = messages[messages.length - 1] || { role: 'assistant', content: '' };
    messages = [...messages.slice(0, -1), { ...last, content }];
  }

  function handleChatScroll() {
    if (!scrollArea) return;
    const distanceFromBottom = scrollArea.scrollHeight - scrollArea.scrollTop - scrollArea.clientHeight;
    followOutput = distanceFromBottom < 80;
  }

  function attach(path) {
    if (!contextPaths.includes(path)) contextPaths = [...contextPaths, path];
    addMenuOpen = false;
  }

  async function uploadFiles(selected) {
    uploadError = '';
    try {
      for (const file of Array.from(selected || [])) {
        const body = new FormData();
        body.append('file', file);
        const created = await api('/api/ingest/upload', { method: 'POST', body });
        attach(created.path);
      }
      input = 'Review the attached source. Summarize its key contribution and tell me which Wiki pages it should affect.';
      await onFilesChanged();
    } catch (cause) { uploadError = cause.message; }
    finally { if (fileInput) fileInput.value = ''; }
  }

  async function addUrl() {
    if (!url.trim()) return;
    addingUrl = true;
    uploadError = '';
    try {
      const created = await api('/api/ingest/url', { method: 'POST', body: JSON.stringify({ url: url.trim() }) });
      url = '';
      attach(created.path);
      input = 'Review the downloaded website and tell me what context is still needed before integration.';
      await onFilesChanged();
    } catch (cause) { uploadError = cause.message; }
    finally { addingUrl = false; }
  }

  function fileAnswer() {
    input = 'File the durable knowledge from the answer above into the Wiki. Update the relevant pages, cross-links, and provenance.';
  }

  async function undo(operation) {
    undoing = operation.id;
    try {
      const updated = await api(`/api/operations/${operation.id}/undo`, { method: 'POST' });
      messages = messages.map((message) => message.operation?.id === updated.id ? { ...message, operation: updated } : message);
      await onFilesChanged();
    } catch (cause) { uploadError = cause.message; }
    finally { undoing = ''; }
  }

  function age(value) {
    const days = Math.floor((Date.now() - new Date(value).getTime()) / 86400000);
    return days <= 0 ? 'today' : days === 1 ? 'yesterday' : `${days}d ago`;
  }
</script>

<svelte:window onclick={() => threadContextMenu = null} onkeydown={(event) => { if (event.key === 'Escape') threadContextMenu = null; }} />

<section class="chat-workspace page-enter karpathy-chat">
  <aside class="thread-rail">
    <div class="rail-heading"><span>Conversations</span><button onclick={newChat} aria-label="New chat"><MessageSquarePlus size={16} /></button></div>
    <button class="new-chat-button" onclick={newChat}><Plus size={15} /> New conversation</button>
    <div class="thread-list">
      {#each threads as thread}
        <button class:active={thread.id === threadId} onclick={() => openThread(thread.id)} oncontextmenu={(event) => showThreadContextMenu(event, thread)}><strong>{thread.title}</strong><small>{thread.message_count} messages · {age(thread.updated_at)}</small></button>
      {/each}
      {#if !threads.length}<p class="rail-empty">Conversations are working context. Durable knowledge is filed into Wiki.</p>{/if}
    </div>
  </aside>

  <div class="chat-main">
    <header class="operation-header">
      <div class="unified-agent-heading"><span>ONE AGENT · ONE CONVERSATION</span><strong>Work with your Wiki</strong><small>It reads, investigates, and writes only when the request calls for it.</small></div>
      <span class:online={authStatus.authenticated} class="connection-pill"><i></i>{authStatus.authenticated ? 'Codex ready' : 'Local only'}</span>
    </header>

    <div class="chat-scroll" bind:this={scrollArea} onscroll={handleChatScroll}>
      {#if !messages.length}
        <div class="chat-welcome operation-welcome">
          <span class="welcome-mark"><Bot size={25} /></span>
          <p class="eyebrow">A CONVERSATIONAL WIKI AGENT</p>
          <h1>What do you want to do with your Wiki?</h1>
          <p>Ask a question, attach a source, request an integration, or repair the knowledge structure. Locus decides which tools the conversation needs.</p>
          {#if !authStatus.authenticated}
            <button class="connect-callout" onclick={onLogin}><KeyRound size={18} /><span><strong>Connect Codex to begin</strong><small>Authorize with your ChatGPT account</small></span></button>
          {:else}
            <div class="work-starters three-starters"><button onclick={() => ask('What does the Wiki currently know, and where are its biggest gaps?')}><BookOpenText size={17} /><span><strong>Orient me</strong><small>Read the compiled Wiki first</small></span></button><button onclick={() => fileInput.click()}><FilePlus2 size={17} /><span><strong>Add a source</strong><small>Review it before integrating</small></span></button><button onclick={() => ask('Check the Wiki for broken links, orphans, missing provenance, index gaps, and unprocessed sources. Explain what matters most.')}><FileInput size={17} /><span><strong>Check Wiki health</strong><small>Inspect structure and sources</small></span></button></div>
            {#if unprocessed.length}<div class="source-queue">{#each unprocessed.slice(0,4) as source}<button onclick={() => { attach(source.path); input = `Review [[${source.path}]] and tell me what it contributes. Do not integrate it until I ask.`; }}><span class="status-dot"></span><span><strong>{source.title}</strong><small>{source.path}</small></span><ChevronRight size={14} /></button>{/each}</div>{/if}
          {/if}
        </div>
      {:else}
        <div class="chat-messages">
          {#each messages as message, index}
            {#if message.role === 'operation'}
              <article class="operation-receipt">
                <header><span class:undone={message.operation.status === 'undone'}><Check size={15} /></span><div><strong>Wiki update</strong><small>{message.operation.status} · {message.operation.kind}</small></div></header>
                {#if message.operation.sources?.length}<div class="receipt-row"><span>Sources</span><div>{#each message.operation.sources as source}<button onclick={() => onOpenSource(files.find((file) => file.path === source.path) || { path: source.path, kind: 'markdown' })}>{source.path}</button>{/each}</div></div>{/if}
                <div class="receipt-row"><span>Changes</span><div>{#each message.operation.changes || [] as change}<button onclick={() => onOpenSource({ path: change.path, kind: 'markdown', space: 'wiki' })}><b>{change.action}</b>{change.path}</button>{/each}{#if !message.operation.changes?.length}<em>No Wiki files changed.</em>{/if}</div></div>
                {#if message.operation.status === 'completed' && message.operation.changes?.length}<button class="undo-button" disabled={undoing === message.operation.id} onclick={() => undo(message.operation)}><RotateCcw size={13} /> {undoing === message.operation.id ? 'Undoing…' : 'Undo operation'}</button>{/if}
              </article>
            {:else}
              <article class:assistant={message.role === 'assistant'} class="chat-message">
                <span class="message-avatar">{#if message.role === 'assistant'}<Bot size={16} />{:else}<UserRound size={16} />{/if}</span>
                <div><span class="message-role">{message.role === 'assistant' ? 'Locus' : 'You'}</span>{#if message.role === 'assistant'}{#if message.activities?.length}<AgentActivity activities={message.activities} streaming={streaming && index === messages.length - 1} />{/if}<Markdown content={message.content || ' '} compact />{#if !streaming && index === messages.length - 1}<button class="file-answer" onclick={fileAnswer}><BookOpenText size={13} /> File durable knowledge to Wiki</button>{/if}{:else}<p>{message.content}</p>{/if}</div>
              </article>
            {/if}
          {/each}
          {#if streaming && statusMessage && !messages[messages.length - 1]?.activities?.length}<div class="agent-status"><span class="thinking-dots"><i></i><i></i><i></i></span>{statusMessage}</div>{/if}
        </div>
      {/if}
    </div>

    <div class="composer-wrap">
      {#if contextPaths.length}<div class="context-chips">{#each contextPaths as path}<span><Paperclip size={11} />{path}<button onclick={() => contextPaths = contextPaths.filter((item) => item !== path)}><X size={11} /></button></span>{/each}</div>{/if}
      {#if addMenuOpen}
        <div class="add-source-menu">
          <header><strong>Add context or source</strong><button onclick={() => addMenuOpen = false}><X size={14} /></button></header>
          <button onclick={() => fileInput.click()}><FilePlus2 size={16} /><span><strong>Upload source</strong><small>Stored immutably under ingest/</small></span></button>
          <form onsubmit={(event) => { event.preventDefault(); addUrl(); }}><label><Link2 size={14} /> Download website</label><div><input bind:value={url} type="url" placeholder="https://…" /><button disabled={!url.trim() || addingUrl}>{addingUrl ? 'Downloading…' : 'Download'}</button></div></form>
          {#if unprocessed.length}<span class="menu-label">Unprocessed</span>{#each unprocessed as source}<button onclick={() => attach(source.path)}><span class="status-dot"></span><span><strong>{source.title}</strong><small>{source.path}</small></span></button>{/each}{/if}
          {#if uploadError}<p class="upload-error">{uploadError}</p>{/if}
        </div>
      {/if}
      <form class="chat-composer" onsubmit={(event) => { event.preventDefault(); ask(); }}>
        <textarea bind:value={input} onkeydown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); ask(); } }} placeholder={authStatus.authenticated ? 'Ask, investigate, integrate, or maintain…' : 'Connect Codex to begin…'} rows="3" disabled={streaming}></textarea>
        <div class="composer-footer"><div><button type="button" class:active={addMenuOpen} class="attach-button" onclick={() => addMenuOpen = !addMenuOpen}><Plus size={15} /> Add</button><span>Reads by default · writes are tracked and reversible</span></div><button class="send-button" type="submit" disabled={!input.trim() || streaming}>{#if streaming}<LoaderCircle class="spin" size={16} />{:else}<ArrowUp size={17} />{/if}</button></div>
      </form>
    </div>
    <input bind:this={fileInput} class="hidden-input" type="file" multiple accept=".pdf,.md,.txt,.html,.htm,.csv,.json" onchange={(event) => uploadFiles(event.currentTarget.files)} />
  </div>
</section>

{#if threadContextMenu}
  <div class="tree-context-menu" style={`left:${threadContextMenu.x}px;top:${threadContextMenu.y}px`} role="menu">
    <button class="danger" disabled={streaming} onclick={(event) => { event.stopPropagation(); deleteThread(threadContextMenu.thread); }}><Trash2 size={14} /> Delete conversation</button>
  </div>
{/if}
{#if threadError}<div class="toast error-toast">{threadError}</div>{/if}

<script>
  import { onMount } from 'svelte';
  import { Command, Download, FilePlus2, LoaderCircle, Menu, Search, X } from '@lucide/svelte';
  import AuthModal from './AuthModal.svelte';
  import ChatPage from './ChatPage.svelte';
  import NotesWorkspace from './NotesWorkspace.svelte';
  import SettingsModal from './SettingsModal.svelte';
  import Sidebar from './Sidebar.svelte';
  import { api, getAuthStatus, getFiles, searchNotes } from './api.js';

  let files = [];
  let directories = [];
  let authStatus = { authenticated: false };
  let loading = true;
  let route = parseRoute();
  let mobileSidebar = false;
  let authModalOpen = false;
  let settingsModalOpen = false;
  let searchOpen = false;
  let searchQuery = '';
  let searchResults = [];
  let searching = false;
  let createModalOpen = false;
  let newPath = '';
  let newSpace = 'manual';
  let newError = '';
  let chatPrompt = '';
  let chatContext = [];
  let installPrompt = null;
  let installed = false;

  onMount(() => {
    refreshAll();
    installed = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    const onHash = () => { route = parseRoute(); mobileSidebar = false; searchOpen = false; };
    const onInstallAvailable = (event) => {
      event.preventDefault();
      installPrompt = event;
    };
    const onAppInstalled = () => {
      installed = true;
      installPrompt = null;
    };
    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); searchOpen = true;
      }
      if (event.key === 'Escape') { searchOpen = false; createModalOpen = false; settingsModalOpen = false; }
    };
    window.addEventListener('hashchange', onHash);
    window.addEventListener('keydown', onKey);
    window.addEventListener('beforeinstallprompt', onInstallAvailable);
    window.addEventListener('appinstalled', onAppInstalled);
    return () => {
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('beforeinstallprompt', onInstallAvailable);
      window.removeEventListener('appinstalled', onAppInstalled);
    };
  });

  async function installApp() {
    if (!installPrompt) return;
    installPrompt.prompt();
    await installPrompt.userChoice;
    installPrompt = null;
  }

  async function refreshAll() {
    try {
      const [fileData, nextAuth] = await Promise.all([getFiles(), getAuthStatus()]);
      files = fileData.files;
      directories = fileData.directories || [];
      authStatus = nextAuth;
    } finally {
      loading = false;
    }
  }

  async function refreshFiles() {
    const fileData = await getFiles();
    files = fileData.files;
    directories = fileData.directories || [];
  }

  function parseRoute() {
    const hash = location.hash.replace(/^#\/?/, '');
    if (!hash || hash === 'chat') return { view: 'chat' };
    const [view, ...parts] = hash.split('/');
    if (view === 'notes') return { view: 'notes', space: ['manual', 'ingest', 'wiki'].includes(parts[0]) ? parts[0] : '' };
    if (view === 'note') return { view: 'notes', path: decodeURIComponent(parts.join('/')), kind: 'markdown' };
    if (view === 'ingest') return { view: 'notes', path: decodeURIComponent(parts.join('/')), kind: 'asset', space: 'ingest' };
    return { view: 'chat' };
  }

  function navigate(view, value = '', preserveChatRequest = false) {
    if (view === 'chat') {
      if (!preserveChatRequest) { chatPrompt = ''; chatContext = []; }
      location.hash = '#/chat';
    }
    else if (view === 'notes') location.hash = value ? `#/notes/${value}` : '#/notes';
  }

  function openFile(file) {
    location.hash = file.kind === 'asset'
      ? `#/ingest/${encodeURIComponent(file.path)}`
      : `#/note/${encodeURIComponent(file.path)}`;
  }

  function openChat(request) {
    chatPrompt = request.prompt || '';
    chatContext = request.context || [];
    navigate('chat', '', true);
  }

  async function runSearch() {
    if (!searchQuery.trim()) { searchResults = []; return; }
    searching = true;
    try {
      ({ results: searchResults } = await searchNotes(searchQuery));
    } finally {
      searching = false;
    }
  }

  let searchTimer;
  $: {
    searchQuery;
    clearTimeout(searchTimer);
    if (searchOpen) searchTimer = setTimeout(runSearch, 180);
  }

  function openCreateNote(request = {}) {
    newSpace = request.space || 'manual';
    const base = request.basePath || newSpace;
    newPath = base ? `${base.replace(/\/$/, '')}/` : '';
    newError = '';
    createModalOpen = true;
  }

  async function createNote() {
    newError = '';
    try {
      const raw = newPath.trim();
      const cleanPath = raw.endsWith('.md') ? raw : `${raw}.md`;
      if (!raw || raw.endsWith('/')) throw new Error('Enter a filename for the note');
      const title = cleanPath.split('/').pop().replace(/\.md$/i, '').replace(/[-_]/g, ' ');
      const note = await api('/api/notes', {
        method: 'POST', body: JSON.stringify({ path: cleanPath, content: `# ${title}\n\n` })
      });
      createModalOpen = false;
      newPath = '';
      await refreshFiles();
      openFile({ ...note, kind: 'markdown' });
    } catch (cause) {
      newError = cause.message;
    }
  }

  function resultFile(result) {
    return files.find((file) => file.path === result.path) || {
      ...result, kind: result.source_type && !result.path.toLowerCase().endsWith('.md') ? 'asset' : 'markdown'
    };
  }
</script>

<div class="app-shell">
  <div class:mobile-open={mobileSidebar} class="sidebar-wrap">
    <Sidebar {route} {authStatus} onNavigate={navigate} onConnect={() => authModalOpen = true} onSettings={() => settingsModalOpen = true} onClose={() => mobileSidebar = false} />
  </div>
  {#if mobileSidebar}<button class="mobile-scrim" onclick={() => mobileSidebar = false} aria-label="Close navigation"></button>{/if}

  <div class="workspace-shell">
    <header class="topbar">
      <div class="topbar-left"><button class="icon-button mobile-menu" onclick={() => mobileSidebar = true}><Menu size={18} /></button><span class="page-location">{route.view === 'chat' ? 'Chat' : 'Notes'}</span></div>
      <button class="search-trigger" onclick={() => searchOpen = true}><Search size={16} /><span>Search Manual, Ingest, and Wiki</span><kbd><Command size={11} /> K</kbd></button>
      <div class="topbar-actions">
        {#if installPrompt && !installed}
          <button class="topbar-new" onclick={installApp} title="Install Locus on this computer"><Download size={14} /> Install app</button>
        {/if}
      </div>
    </header>

    <main class="main-content">
      {#if loading}
        <div class="loading-state full"><LoaderCircle class="spin" size={25} /> Preparing your knowledge spaces…</div>
      {:else}
        <div class="route-view" hidden={route.view !== 'chat'}>
          <ChatPage {authStatus} {files} initialPrompt={chatPrompt} initialContext={chatContext} onLogin={() => authModalOpen = true} onFilesChanged={refreshFiles} onOpenSource={openFile} />
        </div>
        <div class="route-view" hidden={route.view !== 'notes'}>
          <NotesWorkspace {files} {directories} selectedPath={route.view === 'notes' ? route.path || '' : ''} onOpen={openFile} onNewNote={openCreateNote} onDeleted={() => navigate('notes')} onChat={openChat} onChanged={refreshFiles} />
        </div>
      {/if}
    </main>
  </div>
</div>

{#if searchOpen}
  <div class="command-backdrop">
    <button class="backdrop-dismiss" onclick={() => searchOpen = false} aria-label="Close search"></button>
    <div class="command-palette">
      <div class="command-input"><Search size={20} /><input bind:value={searchQuery} placeholder="Search every knowledge space…" /><button onclick={() => searchOpen = false}><X size={17} /></button></div>
      <div class="command-results">
        {#if searching}<div class="command-empty"><LoaderCircle class="spin" size={18} /> Searching…</div>
        {:else if searchResults.length}
          {#each searchResults as result}
            <button onclick={() => openFile(resultFile(result))}><span class={`result-space ${result.space}`}>{result.space.slice(0, 1).toUpperCase()}</span><span><strong>{result.title}</strong><small>{result.path} · {result.excerpt}</small></span><kbd>↵</kbd></button>
          {/each}
        {:else if searchQuery}<div class="command-empty">No matching knowledge.</div>
        {:else}<div class="command-empty"><span>Search source material and synthesized pages together.</span><small>Full-text plus local similarity search</small></div>{/if}
      </div>
    </div>
  </div>
{/if}

{#if createModalOpen}
  <div class="modal-backdrop">
    <button class="backdrop-dismiss" onclick={() => createModalOpen = false} aria-label="Close new note dialog"></button>
    <form class="create-modal" onsubmit={(event) => { event.preventDefault(); createNote(); }}>
      <div class="auth-icon"><FilePlus2 size={23} /></div><p class="eyebrow">NEW {newSpace.toUpperCase()} NOTE</p><h2>{newSpace === 'wiki' ? 'Add a Wiki page directly.' : 'Write something of your own.'}</h2><p>{newSpace === 'wiki' ? 'Direct edits are allowed, but do not receive agent provenance or an undo receipt.' : 'Manual notes live below manual/ and are never rewritten by the agent unless you explicitly ask.'}</p>
      <label>Relative Markdown path<input bind:value={newPath} placeholder="manual/research/new-idea.md" /></label>
      {#if newError}<p class="form-error">{newError}</p>{/if}
      <div class="modal-actions"><button type="button" class="secondary-button" onclick={() => createModalOpen = false}>Cancel</button><button class="primary-button" disabled={!newPath.trim()}>Create note</button></div>
    </form>
  </div>
{/if}

{#if settingsModalOpen}
  <SettingsModal on:close={() => settingsModalOpen = false} />
{/if}

{#if authModalOpen}
  <AuthModal
    {authStatus}
    on:close={() => authModalOpen = false}
    on:authenticated={async () => { authModalOpen = false; authStatus = await getAuthStatus(); }}
    on:disconnected={async () => { authModalOpen = false; authStatus = await getAuthStatus(); }}
  />
{/if}

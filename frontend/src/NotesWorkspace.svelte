<script>
  import {
    BookOpenText,
    CalendarClock,
    Check,
    ChevronDown,
    ChevronRight,
    ExternalLink,
    File,
    FileCode2,
    FileInput,
    FilePenLine,
    FilePlus2,
    FileSpreadsheet,
    FileText,
    Folder,
    FolderOpen,
    FolderPlus,
    Link2,
    LoaderCircle,
    MessageCircle,
    Pencil,
    Save,
    Search,
    Trash2,
    Upload,
    X
  } from '@lucide/svelte';
  import Markdown from './Markdown.svelte';
  import MarkdownEditor from './MarkdownEditor.svelte';
  import SpreadsheetViewer from './SpreadsheetViewer.svelte';
  import { api, getIngestItem, getNote, getSpreadsheet } from './api.js';

  export let files = [];
  export let directories = [];
  export let selectedPath = '';
  export let onOpen = () => {};
  export let onNewNote = () => {};
  export let onDeleted = () => {};
  export let onChat = () => {};
  export let onChanged = () => {};

  const spaceMeta = {
    manual: { label: 'Manual', description: 'Your authored notes, preserved as written', icon: FilePenLine },
    ingest: { label: 'Ingest', description: 'Raw external material and downloaded websites', icon: FileInput },
    wiki: { label: 'Wiki', description: 'Interlinked synthesis maintained with the agent', icon: BookOpenText }
  };

  let filter = '';
  let expanded = new Set(['manual:', 'ingest:', 'wiki:']);
  let item = null;
  let loadedPath = '';
  let loading = false;
  let error = '';
  let editing = false;
  let draft = '';
  let saving = false;
  let saved = false;
  let contextMenu = null;
  let spreadsheetInput;
  let spreadsheetFolder = 'manual';

  $: tree = buildTree(files, directories, filter, expanded);
  $: if (selectedPath && selectedPath !== loadedPath) load(selectedPath);
  $: if (!selectedPath && loadedPath) {
    loadedPath = '';
    item = null;
    error = '';
  }
  $: selectedFile = files.find((file) => file.path === selectedPath);

  async function load(path) {
    loadedPath = path;
    loading = true;
    error = '';
    editing = false;
    try {
      const file = files.find((candidate) => candidate.path === path);
      item = file?.kind === 'asset'
        ? await getIngestItem(path)
        : file?.kind === 'spreadsheet'
          ? await getSpreadsheet(path)
          : await getNote(path);
      draft = item.content || '';
    } catch (cause) {
      error = cause.message;
      item = null;
    } finally {
      loading = false;
    }
  }

  function buildTree(sourceFiles, sourceDirectories, query, openFolders) {
    const normalizedQuery = query.trim().toLowerCase();
    if (normalizedQuery) {
      return sourceFiles
        .filter((file) => `${file.title} ${file.path}`.toLowerCase().includes(normalizedQuery))
        .map((file) => ({
          type: 'file', file, depth: 0, name: file.title,
          ingestRoot: file.space === 'ingest', ingestedAt: file.ingested_at
        }))
        .sort((a, b) => {
          if (a.file.space === 'ingest' && b.file.space === 'ingest') {
            const chronology = Date.parse(b.ingestedAt || 0) - Date.parse(a.ingestedAt || 0);
            if (chronology) return chronology;
          }
          return a.name.localeCompare(b.name);
        });
    }
    const rows = [];
    const directoryByPath = new Map(sourceDirectories.map((directory) => [directory.path, directory]));
    for (const space of ['manual', 'ingest', 'wiki']) {
      const group = sourceFiles.filter((file) => file.space === space);
      const rootKey = `${space}:`;
      rows.push({ type: 'space', space, depth: 0, count: group.length, key: rootKey });
      if (!openFolders.has(rootKey)) continue;
      const directories = new Map();
      const filesAt = new Map();
      for (const item of sourceDirectories.filter((directory) => directory.space === space)) {
        let relative = item.path;
        if (relative.toLowerCase().startsWith(`${space}/`)) relative = relative.slice(space.length + 1);
        const parts = relative.split('/').filter(Boolean);
        for (let index = 1; index <= parts.length; index += 1) {
          const dir = parts.slice(0, index).join('/');
          const parentDir = parts.slice(0, index - 1).join('/');
          if (!directories.has(parentDir)) directories.set(parentDir, new Set());
          directories.get(parentDir).add(dir);
        }
      }
      for (const file of group) {
        let relative = file.path;
        if (relative.toLowerCase().startsWith(`${space}/`)) relative = relative.slice(space.length + 1);
        const parts = relative.split('/');
        const parent = parts.slice(0, -1).join('/');
        if (!filesAt.has(parent)) filesAt.set(parent, []);
        filesAt.get(parent).push({ file, name: parts.at(-1) });
        for (let index = 1; index < parts.length; index += 1) {
          const dir = parts.slice(0, index).join('/');
          const parentDir = parts.slice(0, index - 1).join('/');
          if (!directories.has(parentDir)) directories.set(parentDir, new Set());
          directories.get(parentDir).add(dir);
        }
      }
      const visit = (parent, depth) => {
        const childDirectories = Array.from(directories.get(parent) || []).map((directory) => {
          const path = `${space}/${directory}`;
          const metadata = directoryByPath.get(path) || {};
          return {
            type: 'folder', space, key: `${space}:${directory}`,
            name: directory.split('/').at(-1), depth, path,
            ingestRoot: space === 'ingest' && parent === '' && metadata.is_ingest_group,
            ingestedAt: metadata.ingested_at
          };
        });
        const childFiles = (filesAt.get(parent) || []).map((entry) => ({
          type: 'file', file: entry.file, name: entry.name, depth,
          ingestRoot: space === 'ingest' && parent === '',
          ingestedAt: entry.file.ingested_at
        }));
        const children = [...childDirectories, ...childFiles].sort((a, b) => {
          if (space === 'ingest' && parent === '') {
            const chronology = Date.parse(b.ingestedAt || 0) - Date.parse(a.ingestedAt || 0);
            if (chronology) return chronology;
          }
          if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
          return a.name.localeCompare(b.name);
        });
        for (const child of children) {
          rows.push(child);
          if (child.type === 'folder' && openFolders.has(child.key)) {
            const directory = child.path.slice(space.length + 1);
            visit(directory, depth + 1);
          }
        }
      };
      visit('', 1);
    }
    return rows;
  }

  function toggle(key) {
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key); else next.add(key);
    expanded = next;
  }

  function fileIcon(file) {
    if (file.extension === '.md') return FileText;
    if (file.kind === 'spreadsheet') return FileSpreadsheet;
    if (['.html', '.htm', '.json'].includes(file.extension)) return FileCode2;
    return File;
  }

  function formatIngestedAt(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'
    }).format(date);
  }

  async function save() {
    if (!item || item.space === 'ingest') return;
    saving = true;
    error = '';
    try {
      item = await api(`/api/notes/${encodeURIComponent(item.path)}`, {
        method: 'PUT', body: JSON.stringify({ content: draft })
      });
      editing = false;
      saved = true;
      setTimeout(() => saved = false, 1600);
      await onChanged();
    } catch (cause) {
      error = cause.message;
    } finally {
      saving = false;
    }
  }

  function openFile(file) {
    onOpen(file);
  }

  function openRoot(row) {
    if (row.space === 'wiki') {
      const next = new Set(expanded);
      next.add(row.key);
      expanded = next;
      const index = files.find((file) => file.path === 'wiki/index.md');
      if (index) openFile(index);
      return;
    }
    toggle(row.key);
  }

  function showContextMenu(event, row) {
    event.preventDefault();
    event.stopPropagation();
    contextMenu = {
      row,
      x: Math.min(event.clientX, window.innerWidth - 220),
      y: Math.min(event.clientY, window.innerHeight - 260)
    };
  }

  function rowSpace(row) {
    return row.type === 'file' ? row.file.space : row.space;
  }

  function rowPath(row) {
    if (row.type === 'file') return row.file.path;
    if (row.type === 'folder') return row.path;
    return row.space;
  }

  function parentPath(path) {
    return path.includes('/') ? path.split('/').slice(0, -1).join('/') : '';
  }

  function creationBase(row) {
    return row.type === 'file' ? parentPath(row.file.path) : rowPath(row);
  }

  function canCreateNote(row) {
    return rowSpace(row) !== 'ingest';
  }

  function canImportSpreadsheet(row) {
    return rowSpace(row) === 'manual';
  }

  function canMutate(row) {
    return row.type !== 'space' && !['wiki/index.md', 'wiki/log.md'].includes(rowPath(row));
  }

  function requestNewNote(row) {
    contextMenu = null;
    onNewNote({ space: rowSpace(row), basePath: creationBase(row) });
  }

  function requestSpreadsheet(row) {
    contextMenu = null;
    spreadsheetFolder = creationBase(row) || 'manual';
    spreadsheetInput?.click();
  }

  async function uploadSpreadsheet(event) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    form.append('folder', spreadsheetFolder);
    try {
      const created = await api('/api/manual/spreadsheet', { method: 'POST', body: form });
      await onChanged();
      onOpen(created);
    } catch (cause) { error = cause.message; }
  }

  async function createFolder(row) {
    contextMenu = null;
    const name = window.prompt('Folder name');
    if (!name?.trim()) return;
    if (/[\\/]/.test(name.trim())) { error = 'Enter one folder name without slashes.'; return; }
    const base = creationBase(row);
    const path = [base, name.trim()].filter(Boolean).join('/');
    try {
      await api('/api/files/directories', { method: 'POST', body: JSON.stringify({ path }) });
      await onChanged();
    } catch (cause) { error = cause.message; }
  }

  async function renameNode(row) {
    contextMenu = null;
    const source = rowPath(row);
    const currentName = source.split('/').at(-1);
    const name = window.prompt('New name', currentName);
    if (!name?.trim() || name.trim() === currentName) return;
    if (/[\\/]/.test(name.trim())) { error = 'Enter one name without slashes.'; return; }
    const target = [parentPath(source), name.trim()].filter(Boolean).join('/');
    try {
      const moved = await api('/api/files/move', {
        method: 'POST',
        body: JSON.stringify({ source_path: source, target_path: target, is_directory: row.type === 'folder' })
      });
      await onChanged();
      if (row.type === 'file' && selectedPath === source) onOpen({ ...row.file, path: moved.path });
      else if (row.type === 'folder' && (selectedPath === source || selectedPath.startsWith(`${source}/`))) onDeleted();
    } catch (cause) { error = cause.message; }
  }

  async function deleteNode(row) {
    contextMenu = null;
    const path = rowPath(row);
    const label = row.type === 'folder' ? `Delete the empty folder “${path}”?` : `Delete “${path}”? This cannot be undone in Locus.`;
    if (!window.confirm(label)) return;
    try {
      const endpoint = row.type === 'folder' ? `/api/directories/${encodeURIComponent(path)}` : `/api/files/${encodeURIComponent(path)}`;
      await api(endpoint, { method: 'DELETE' });
      await onChanged();
      if (selectedPath === path || (row.type === 'folder' && selectedPath.startsWith(`${path}/`))) onDeleted();
    } catch (cause) { error = cause.message; }
  }
</script>

<svelte:window onclick={() => contextMenu = null} onkeydown={(event) => { if (event.key === 'Escape') contextMenu = null; }} />

<section class="notes-workspace page-enter">
  <aside class="file-explorer">
    <div class="explorer-heading"><span><FolderOpen size={16} /> Files</span><small>Right-click to manage</small></div>
    <label class="tree-search"><Search size={14} /><input bind:value={filter} placeholder="Filter files" />{#if filter}<button onclick={() => filter = ''}><X size={13} /></button>{/if}</label>
    <div class="file-tree">
      {#each tree as row}
        {#if row.type === 'space'}
          <button class="tree-space" onclick={() => openRoot(row)} oncontextmenu={(event) => showContextMenu(event, row)}>
            {#if expanded.has(row.key)}<ChevronDown size={14} />{:else}<ChevronRight size={14} />{/if}
            <span class={`space-icon ${row.space}`}><svelte:component this={spaceMeta[row.space].icon} size={13} /></span><strong>{spaceMeta[row.space].label}</strong><small>{row.count}</small>
          </button>
        {:else if row.type === 'folder'}
          <button class:ingest-root={row.ingestRoot} class="tree-folder" style={`padding-left:${14 + row.depth * 13}px`} onclick={() => toggle(row.key)} oncontextmenu={(event) => showContextMenu(event, row)}>
            {#if row.ingestRoot && row.ingestedAt}<time class="ingest-time" datetime={row.ingestedAt} title={`Ingested ${new Date(row.ingestedAt).toLocaleString()}`}><CalendarClock size={10} /> {formatIngestedAt(row.ingestedAt)}</time>{/if}
            {#if expanded.has(row.key)}<ChevronDown size={13} /><FolderOpen size={14} />{:else}<ChevronRight size={13} /><Folder size={14} />{/if}<span>{row.name}</span>
          </button>
        {:else}
          <button class:active={selectedPath === row.file.path} class:ingest-root={row.ingestRoot} class="tree-file" style={`padding-left:${23 + row.depth * 13}px`} onclick={() => openFile(row.file)} oncontextmenu={(event) => showContextMenu(event, row)} title={row.file.path}>
            {#if row.ingestRoot && row.ingestedAt}<time class="ingest-time" datetime={row.ingestedAt} title={`Ingested ${new Date(row.ingestedAt).toLocaleString()}`}><CalendarClock size={10} /> {formatIngestedAt(row.ingestedAt)}</time>{/if}
            <svelte:component this={fileIcon(row.file)} size={14} /><span>{row.name}</span>{#if row.file.space === 'ingest'}<i class:integrated={row.file.integration_status === 'integrated'} class="integration-dot" title={row.file.integration_status}></i>{/if}
          </button>
        {/if}
      {/each}
      {#if !tree.length}<p class="tree-empty">No matching files.</p>{/if}
    </div>
  </aside>

  <div class="file-viewer">
    {#if loading}
      <div class="viewer-state"><LoaderCircle class="spin" size={23} /> Opening file…</div>
    {:else if error && !item}
      <div class="viewer-state error-state">{error}</div>
    {:else if item}
      <div class="viewer-body">
        <div class="viewer-breadcrumb">{item.path}</div>
        <div class="file-title-row">
          <div><span class={`space-label ${item.space}`}>{item.space}</span><h1>{item.title}</h1><p>{item.kind === 'spreadsheet' ? `${item.sheets.length} sheet${item.sheets.length === 1 ? '' : 's'}` : `${item.word_count?.toLocaleString() || 0} words`} · {(item.size / 1024).toFixed(1)} KB</p></div>
          <div class="viewer-actions">
            {#if saved}<span class="saved-label"><Check size={13} /> Saved</span>{/if}
            {#if item.kind === 'asset'}
              <button class="secondary-button" onclick={() => onChat({ prompt: `Answer questions about [[${item.path}]].`, context: [item.path] })}><MessageCircle size={14} /> Ask</button><button class="primary-button" onclick={() => onChat({ prompt: `Integrate [[${item.path}]] into the Wiki. Update every relevant durable page, cross-link related knowledge, and preserve provenance.`, context: [item.path] })}>Integrate</button><a class="secondary-button" href={`/api/ingest/files/${encodeURIComponent(item.path)}`} target="_blank" rel="noreferrer">Original <ExternalLink size={14} /></a>
            {:else if editing}
              <button class="secondary-button" onclick={() => { editing = false; draft = item.content; }}><X size={14} /> Cancel</button><button class="primary-button" onclick={save} disabled={saving}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</button>
            {:else}
              <button class="secondary-button" onclick={() => onChat({ prompt: `Work with [[${item.path}]].`, context: [item.path] })}><MessageCircle size={14} /> Discuss</button>
              {#if item.space === 'ingest'}<button class="primary-button" onclick={() => onChat({ prompt: `Integrate [[${item.path}]] into the Wiki. Update every relevant durable page, cross-link related knowledge, and preserve provenance.`, context: [item.path] })}>Integrate</button>{/if}
              {#if item.space === 'manual'}<button class="secondary-button" onclick={() => onChat({ prompt: `Integrate the durable knowledge in [[${item.path}]] into the Wiki.`, context: [item.path] })}>Integrate</button>{/if}
              {#if item.space === 'wiki'}<button class="secondary-button" onclick={() => onChat({ prompt: `Maintain [[${item.path}]]. Check its provenance, links, and consistency with the rest of the Wiki.`, context: [item.path] })}>Maintain</button>{/if}
              {#if selectedFile?.editable}<button class="secondary-button" onclick={() => { editing = true; }}>Edit visually</button>{/if}
            {/if}
          </div>
        </div>
        {#if item.extraction_error}<p class="extraction-warning">{item.extraction_error}</p>{/if}
        {#if editing}
          <MarkdownEditor content={draft} onChange={(content) => draft = content} />
        {:else if item.kind === 'asset'}
          <article class="extracted-source">
            <span class="section-label">{item.media_type === 'text/html' ? 'DOCLING MARKDOWN' : 'EXTRACTED TEXT'}</span>
            {#if item.media_type === 'text/html'}
              <div class="reading-pane"><Markdown content={item.content || 'No searchable content could be extracted from this website.'} /></div>
            {:else}
              <pre>{item.content || 'No searchable text could be extracted from this file.'}</pre>
            {/if}
          </article>
        {:else if item.kind === 'spreadsheet'}
          <SpreadsheetViewer sheets={item.sheets} />
        {:else}
          <article class="reading-pane"><Markdown content={item.content} /></article>
        {/if}
      </div>
    {:else}
      <div class="notes-overview">
        <p class="eyebrow">THE WIKI IS THE ARTIFACT</p><h1>Browse the sources and the compiled knowledge.</h1><p>Manual and Ingest are source material. Wiki is the interlinked synthesis maintained through Chat. Select a file, or open the Wiki root to start at <code>index.md</code>.</p>
        <div class="knowledge-flow"><span>Manual</span><ChevronRight size={14} /><span>Ingest</span><ChevronRight size={14} /><strong>Wiki</strong><small>The agent synthesizes; sources remain intact.</small></div>
      </div>
    {/if}
    {#if error && item}<div class="toast error-toast">{error}</div>{/if}
  </div>

  {#if item}
    <aside class="file-inspector">
      <div class="inspector-block"><span class="section-label">SPACE</span><h3>{spaceMeta[item.space].label}</h3><p>{spaceMeta[item.space].description}.</p></div>
      {#if item.space === 'ingest'}<div class="inspector-block"><span class="section-label">INTEGRATION</span><h3 class={`integration-state ${item.integration_status}`}>{item.integration_status === 'integrated' ? 'Integrated' : 'Unprocessed'}</h3>{#if item.wiki_pages?.length}<p>Wiki pages affected:</p>{#each item.wiki_pages as page}<button onclick={() => onOpen({ path: page.path, kind: 'markdown', space: 'wiki' })}>{page.path}</button>{/each}{/if}</div>{/if}
      {#if item.provenance?.length}<div class="inspector-block"><span class="section-label">PROVENANCE</span>{#each item.provenance as source}<button onclick={() => onOpen(files.find((file) => file.path === source.path) || { path: source.path, kind: 'markdown' })}>{source.path}</button>{/each}</div>{/if}
      {#if item.backlinks}
        <div class="inspector-block"><span class="section-label"><Link2 size={12} /> BACKLINKS</span>{#each item.backlinks as link}<button onclick={() => onOpen({ path: link.path, kind: 'markdown', space: 'manual' })}>{link.title}</button>{/each}{#if !item.backlinks.length}<p>No backlinks yet.</p>{/if}</div>
      {/if}
      <div class="inspector-block"><span class="section-label">DETAILS</span><dl><dt>Path</dt><dd>{item.path}</dd><dt>Type</dt><dd>{item.media_type || 'Markdown'}</dd>{#if item.space === 'ingest' && item.ingested_at}<dt>Ingested</dt><dd>{new Date(item.ingested_at).toLocaleString()}</dd>{/if}<dt>Indexed</dt><dd>{new Date(item.indexed_at).toLocaleString()}</dd></dl></div>
      {#if item.history?.length}<div class="inspector-block"><span class="section-label">WIKI HISTORY</span>{#each item.history as event}<p><strong>{event.action}</strong> · {event.kind}<br />{new Date(event.created_at).toLocaleDateString()}</p>{/each}</div>{/if}
    </aside>
  {/if}
</section>

<input class="visually-hidden" bind:this={spreadsheetInput} type="file" accept=".ods,.xlsx,.csv" onchange={uploadSpreadsheet} />

{#if contextMenu}
  <div class="tree-context-menu" style={`left:${contextMenu.x}px;top:${contextMenu.y}px`} role="menu">
    {#if canCreateNote(contextMenu.row)}<button onclick={(event) => { event.stopPropagation(); requestNewNote(contextMenu.row); }}><FilePlus2 size={14} /> New note</button>{/if}
    {#if canImportSpreadsheet(contextMenu.row)}<button onclick={(event) => { event.stopPropagation(); requestSpreadsheet(contextMenu.row); }}><Upload size={14} /> Import spreadsheet</button>{/if}
    <button onclick={(event) => { event.stopPropagation(); createFolder(contextMenu.row); }}><FolderPlus size={14} /> New folder</button>
    {#if canMutate(contextMenu.row)}
      <span></span>
      <button onclick={(event) => { event.stopPropagation(); renameNode(contextMenu.row); }}><Pencil size={14} /> Rename</button>
      <button class="danger" onclick={(event) => { event.stopPropagation(); deleteNode(contextMenu.row); }}><Trash2 size={14} /> Delete</button>
    {/if}
  </div>
{/if}

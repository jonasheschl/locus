<script>
  import { Files, KeyRound, MessageSquareText, PanelLeftClose, Settings, Sparkles } from '@lucide/svelte';

  export let route = { view: 'chat' };
  export let authStatus = { authenticated: false };
  export let onNavigate = () => {};
  export let onConnect = () => {};
  export let onSettings = () => {};
  export let onClose = () => {};
</script>

<aside class="sidebar minimal-sidebar">
  <div class="brand-row">
    <button class="brand" onclick={() => onNavigate('chat')} aria-label="Locus chat"><span class="brand-mark"><Sparkles size={15} /></span><span>Locus</span></button>
    <button class="icon-button quiet mobile-close" onclick={onClose} aria-label="Close navigation"><PanelLeftClose size={17} /></button>
  </div>

  <nav class="primary-nav" aria-label="Primary navigation">
    <button class:active={route.view === 'chat'} onclick={() => onNavigate('chat')}><MessageSquareText size={18} /><span><strong>Chat</strong><small>One conversational agent</small></span></button>
    <button class:active={route.view === 'notes'} onclick={() => onNavigate('notes')}><Files size={18} /><span><strong>Notes</strong><small>Browse the files</small></span></button>
  </nav>

  <div class="sidebar-footer">
    <button class="account-button" onclick={onSettings}><span><Settings size={15} /></span><span><strong>Settings</strong><small>Model, reasoning, speed</small></span></button>
    <button class="account-button" onclick={onConnect}><span class:connected={authStatus.authenticated}><KeyRound size={15} /></span><span><strong>{authStatus.authenticated ? 'Codex connected' : 'Connect Codex'}</strong><small>{authStatus.authenticated ? 'Manage account' : 'Use your ChatGPT plan'}</small></span></button>
  </div>
</aside>

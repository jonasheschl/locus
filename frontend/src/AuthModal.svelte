<script>
  import { createEventDispatcher, onDestroy } from 'svelte';
  import { CheckCircle2, Copy, ExternalLink, KeyRound, LoaderCircle, LogOut, ShieldCheck, X } from '@lucide/svelte';
  import { api } from './api.js';

  export let authStatus = { authenticated: false };
  const dispatch = createEventDispatcher();
  let stage = authStatus.authenticated ? 'connected' : 'intro';
  let flow = null;
  let error = '';
  let copied = false;
  let timer;

  onDestroy(() => clearTimeout(timer));

  async function begin() {
    stage = 'loading';
    error = '';
    try {
      flow = await api('/api/auth/codex/device/start', { method: 'POST' });
      stage = 'code';
      schedulePoll(flow.interval_seconds * 1000);
    } catch (cause) {
      error = cause.message;
      stage = 'intro';
    }
  }

  function schedulePoll(delay) {
    clearTimeout(timer);
    timer = setTimeout(poll, Math.max(1000, delay));
  }

  async function poll() {
    if (!flow) return;
    try {
      const result = await api(`/api/auth/codex/device/${flow.flow_id}/poll`, { method: 'POST' });
      if (result.status === 'complete') {
        stage = 'done';
        setTimeout(() => dispatch('authenticated'), 700);
      } else {
        schedulePoll((result.retry_after || flow.interval_seconds) * 1000);
      }
    } catch (cause) {
      error = cause.message;
      stage = 'intro';
    }
  }

  async function copyCode() {
    await navigator.clipboard.writeText(flow.user_code);
    copied = true;
    setTimeout(() => copied = false, 1600);
  }

  async function disconnect() {
    stage = 'loading';
    error = '';
    try {
      await api('/api/auth/codex', { method: 'DELETE' });
      dispatch('disconnected');
    } catch (cause) {
      error = cause.message;
      stage = 'connected';
    }
  }
</script>

<div class="modal-backdrop">
  <button class="backdrop-dismiss" onclick={() => dispatch('close')} aria-label="Close sign-in"></button>
  <div class="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-title">
    <button class="modal-close" onclick={() => dispatch('close')} aria-label="Close"><X size={18} /></button>
    {#if stage === 'intro'}
      <div class="auth-icon"><KeyRound size={25} /></div>
      <p class="eyebrow">CODEX CONNECTION</p>
      <h2 id="auth-title">Bring your own intelligence.</h2>
      <p>Sign in with your ChatGPT account to let Locus reason across your private notes using your Codex allowance.</p>
      <div class="privacy-note"><ShieldCheck size={18} /><span><strong>Local by design.</strong> Your tokens and note index stay in <code>wiki.sqlite3</code> on this machine.</span></div>
      {#if error}<p class="form-error">{error}</p>{/if}
      <button class="auth-primary" onclick={begin}>Continue with OpenAI <ExternalLink size={16} /></button>
      <small class="auth-fineprint">Requires a ChatGPT plan with Codex access. For personal use only.</small>
    {:else if stage === 'loading'}
      <div class="auth-wait"><LoaderCircle class="spin" size={28} /><h2>Preparing secure sign-in…</h2></div>
    {:else if stage === 'code'}
      <div class="auth-icon"><KeyRound size={25} /></div>
      <p class="eyebrow">ONE-TIME DEVICE CODE</p>
      <h2 id="auth-title">Finish in your browser.</h2>
      <p>Open the OpenAI page and enter this code. Locus will notice when you're done.</p>
      <button class="device-code" onclick={copyCode}><span>{flow.user_code}</span>{#if copied}<CheckCircle2 size={18} />{:else}<Copy size={18} />{/if}</button>
      <a class="auth-primary" href={flow.verification_uri} target="_blank" rel="noreferrer">Open OpenAI sign-in <ExternalLink size={16} /></a>
      <div class="polling-label"><span class="pulse-dot"></span> Waiting for authorization…</div>
    {:else if stage === 'done'}
      <div class="auth-success"><CheckCircle2 size={38} /><h2>Codex connected.</h2><p>Your wiki is ready to think with you.</p></div>
    {:else}
      <div class="auth-icon"><ShieldCheck size={25} /></div>
      <p class="eyebrow">CODEX CONNECTION</p>
      <h2 id="auth-title">Your account is connected.</h2>
      <p>{authStatus.account_label || `ChatGPT account …${authStatus.account_id_suffix}`}</p>
      <div class="privacy-note"><ShieldCheck size={18} /><span>The refresh token remains only in your local <code>wiki.sqlite3</code> file.</span></div>
      {#if error}<p class="form-error">{error}</p>{/if}
      <button class="auth-primary" onclick={disconnect}>Disconnect Codex <LogOut size={16} /></button>
    {/if}
  </div>
</div>

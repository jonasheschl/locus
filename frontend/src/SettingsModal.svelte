<script>
  import { createEventDispatcher, onMount } from 'svelte';
  import { Bot, Check, Gauge, LoaderCircle, RefreshCw, Settings, X } from '@lucide/svelte';
  import { api } from './api.js';

  const dispatch = createEventDispatcher();
  let loading = true;
  let saving = false;
  let saved = false;
  let error = '';
  let usage = null;
  let usageLoading = true;
  let usageError = '';
  let data = { model: '', reasoning_effort: 'medium', fast_mode: false, models: [], reasoning_efforts: [] };

  const effortMeta = {
    none: ['None', 'Fastest'],
    low: ['Low', 'Quick'],
    medium: ['Medium', 'Balanced'],
    high: ['High', 'Thorough'],
    xhigh: ['Extra high', 'Deep'],
    max: ['Maximum', 'Hardest work']
  };

  onMount(() => {
    load();
    loadUsage();
  });

  async function load() {
    try { data = await api('/api/settings'); }
    catch (cause) { error = cause.message; }
    finally { loading = false; }
  }

  async function loadUsage() {
    usageLoading = true;
    usageError = '';
    try { usage = await api('/api/auth/codex/usage'); }
    catch (cause) { usage = null; usageError = cause.message; }
    finally { usageLoading = false; }
  }

  function clampPercent(value) {
    return Math.max(0, Math.min(100, Number(value) || 0));
  }

  function windowLabel(minutes, index) {
    if (!minutes) return index === 0 ? 'Primary window' : 'Secondary window';
    if (minutes % 1440 === 0) return `${minutes / 1440}-day window`;
    if (minutes % 60 === 0) return `${minutes / 60}-hour window`;
    return `${minutes}-minute window`;
  }

  function resetLabel(timestamp) {
    if (!timestamp) return 'Reset time unavailable';
    return `Resets ${new Date(timestamp * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`;
  }

  function displayName(value) {
    if (!value) return '';
    return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function chooseModel(model) {
    data = { ...data, model };
    if (model === 'gpt-5.5' && data.reasoning_effort === 'max') {
      data = { ...data, reasoning_effort: 'xhigh' };
    }
  }

  async function save() {
    saving = true;
    error = '';
    try {
      data = await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ model: data.model, reasoning_effort: data.reasoning_effort, fast_mode: data.fast_mode })
      });
      saved = true;
      setTimeout(() => saved = false, 1600);
    } catch (cause) { error = cause.message; }
    finally { saving = false; }
  }
</script>

<div class="modal-backdrop">
  <button class="backdrop-dismiss" onclick={() => dispatch('close')} aria-label="Close settings"></button>
  <div class="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
    <button class="modal-close" onclick={() => dispatch('close')} aria-label="Close"><X size={18} /></button>
    <div class="settings-title"><span><Settings size={20} /></span><div><p class="eyebrow">AGENT SETTINGS</p><h2 id="settings-title">Choose how Locus thinks.</h2></div></div>
    {#if loading}
      <div class="settings-loading"><LoaderCircle class="spin" size={23} /> Loading settings…</div>
    {:else}
      <section class="settings-section">
        <span class="settings-label">Model</span>
        <div class="model-options">
          {#each data.models as model}
            <button class:active={data.model === model} onclick={() => chooseModel(model)}><Bot size={15} /><span><strong>{model}</strong><small>{model.includes('sol') ? 'Frontier capability' : model.includes('terra') ? 'Balanced capability' : model.includes('luna') ? 'Fast and efficient' : 'Previous frontier'}</small></span>{#if data.model === model}<Check size={15} />{/if}</button>
          {/each}
        </div>
      </section>
      <section class="settings-section">
        <span class="settings-label">Reasoning effort</span>
        <div class="effort-options">
          {#each data.reasoning_efforts as effort}
            <button class:active={data.reasoning_effort === effort} disabled={data.model === 'gpt-5.5' && effort === 'max'} onclick={() => data = { ...data, reasoning_effort: effort }}><strong>{effortMeta[effort]?.[0] || effort}</strong><small>{effortMeta[effort]?.[1] || ''}</small></button>
          {/each}
        </div>
        <p>Higher effort can improve difficult synthesis and maintenance work, with greater latency and token use. Availability still depends on your Codex account.</p>
      </section>
      <section class="settings-section fast-setting">
        <div><span><strong>Fast mode</strong><small>Request priority processing for lower latency</small></span><button class:active={data.fast_mode} aria-label="Toggle Fast mode" aria-pressed={data.fast_mode} onclick={() => data = { ...data, fast_mode: !data.fast_mode }}><i></i></button></div>
        <p>Fast mode changes request processing speed, not the selected reasoning effort. It may use a different allowance or rate when available to your account.</p>
      </section>
      <section class="settings-section usage-setting">
        <div class="usage-heading">
          <span><Gauge size={15} /><strong>Codex account usage</strong></span>
          <button class="usage-refresh" onclick={loadUsage} disabled={usageLoading} aria-label="Refresh Codex account usage"><RefreshCw class={usageLoading ? 'spin' : ''} size={13} /> Refresh</button>
        </div>
        {#if usageLoading && !usage}
          <div class="usage-state"><LoaderCircle class="spin" size={16} /> Reading current allowance…</div>
        {:else if usage?.available}
          <div class="usage-summary">
            {#if usage.plan_type}<span>{displayName(usage.plan_type)} plan</span>{/if}
            {#if usage.credits?.unlimited}<span>Unlimited credits</span>{:else if usage.credits?.balance}<span>{usage.credits.balance} credits</span>{/if}
            {#if usage.reset_credits_available}<span>{usage.reset_credits_available} reset{usage.reset_credits_available === 1 ? '' : 's'} available</span>{/if}
          </div>
          {#each usage.limits as limit}
            <div class="usage-limit">
              <div class="usage-limit-title"><strong>{displayName(limit.name)}</strong>{#if limit.limit_reached}<span>Limit reached</span>{/if}</div>
              {#each limit.windows as window, index}
                <div class="usage-window">
                  <div><span>{windowLabel(window.window_minutes, index)}</span><strong>{Math.round(clampPercent(window.used_percent))}% used</strong></div>
                  <div class="usage-meter" role="progressbar" aria-label={`${windowLabel(window.window_minutes, index)} usage`} aria-valuemin="0" aria-valuemax="100" aria-valuenow={clampPercent(window.used_percent)}><i style={`width:${clampPercent(window.used_percent)}%`}></i></div>
                  <small>{resetLabel(window.resets_at)}</small>
                </div>
              {/each}
            </div>
          {/each}
          {#if usage.spend_control?.individual_limit}
            <div class="usage-spend"><span>Workspace allowance</span><strong>{usage.spend_control.individual_limit.remaining ?? '—'} remaining</strong></div>
          {/if}
          <p>Current allowance reported by the connected Codex account. Values can update after a short delay.</p>
        {:else}
          <div class="usage-state usage-unavailable"><span>{usage?.reason || usageError || 'Connect Codex to view account usage.'}</span></div>
          <p>Usage is best-effort because Codex does not expose this personal-account endpoint as a stable public Platform API.</p>
        {/if}
      </section>
      {#if error}<p class="form-error">{error}</p>{/if}
      <div class="settings-actions"><span>{#if saved}<Check size={13} /> Saved{/if}</span><button class="primary-button" disabled={saving || !data.model} onclick={save}>{saving ? 'Saving…' : 'Save settings'}</button></div>
    {/if}
  </div>
</div>

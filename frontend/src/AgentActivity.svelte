<script>
  import {
    Check,
    ChevronDown,
    CircleAlert,
    FilePenLine,
    FileSearch,
    Globe2,
    LoaderCircle,
    Search,
    Sparkles,
    Wrench
  } from '@lucide/svelte';

  export let activities = [];
  export let streaming = false;
  let expanded = true;

  $: completed = activities.filter((activity) => activity.status === 'completed').length;
  $: failed = activities.filter((activity) => activity.status === 'failed').length;
  $: running = activities.find((activity) => activity.status === 'running');
  $: summary = running?.label || (failed ? 'Work finished with an issue' : `${completed} ${completed === 1 ? 'step' : 'steps'} completed`);

  function iconFor(kind) {
    if (kind === 'search_wiki' || kind === 'search_sources') return Search;
    if (kind === 'read_path') return FileSearch;
    if (kind === 'write_wiki_page' || kind === 'update_manual_note') return FilePenLine;
    if (kind === 'download_url') return Globe2;
    if (kind === 'lint_wiki' || kind === 'finalize') return Wrench;
    return Sparkles;
  }
</script>

<div class:active={streaming && !!running} class="agent-activity">
  <button class="activity-summary" type="button" onclick={() => expanded = !expanded} aria-expanded={expanded}>
    {#if running}<LoaderCircle class="spin" size={14} />{:else if failed}<CircleAlert size={14} />{:else}<Check size={14} />{/if}
    <span>{summary}</span>
    <ChevronDown class={expanded ? 'expanded' : ''} size={14} />
  </button>
  {#if expanded}
    <div class="activity-list">
      {#each activities as activity (activity.id)}
        <div class:failed={activity.status === 'failed'} class="activity-row">
          <span class="activity-icon">
            {#if activity.status === 'running'}
              <LoaderCircle class="spin" size={13} />
            {:else if activity.status === 'failed'}
              <CircleAlert size={13} />
            {:else}
              {@const ActivityIcon = iconFor(activity.kind)}
              <ActivityIcon size={13} />
            {/if}
          </span>
          <span><strong>{activity.label}</strong>{#if activity.detail}<small>{activity.detail}</small>{/if}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

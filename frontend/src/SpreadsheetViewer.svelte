<script>
  import { Table2 } from '@lucide/svelte';

  export let sheets = [];
  let activeIndex = 0;

  $: if (activeIndex >= sheets.length) activeIndex = 0;
  $: active = sheets[activeIndex] || { name: 'Sheet1', rows: [] };
  $: width = Math.max(0, ...active.rows.map((row) => row.length));
  $: visibleRows = active.rows.slice(0, 500);

  function columnName(index) {
    let value = index + 1;
    let label = '';
    while (value > 0) {
      value -= 1;
      label = String.fromCharCode(65 + (value % 26)) + label;
      value = Math.floor(value / 26);
    }
    return label;
  }
</script>

<div class="spreadsheet-viewer">
  {#if sheets.length > 1}
    <div class="sheet-tabs" role="tablist" aria-label="Workbook sheets">
      {#each sheets as sheet, index}
        <button role="tab" aria-selected={activeIndex === index} class:active={activeIndex === index} onclick={() => activeIndex = index}><Table2 size={12} /> {sheet.name}</button>
      {/each}
    </div>
  {/if}
  {#if active.rows.length}
    <div class="sheet-grid-wrap">
      <table class="sheet-grid">
        <thead><tr><th class="row-number"></th>{#each Array(width) as _, index}<th>{columnName(index)}</th>{/each}</tr></thead>
        <tbody>
          {#each visibleRows as row, rowIndex}
            <tr><th class="row-number">{rowIndex + 1}</th>{#each Array(width) as _, columnIndex}<td>{row[columnIndex] || ''}</td>{/each}</tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if active.rows.length > visibleRows.length || active.truncated}<p class="sheet-truncated">Showing the first {visibleRows.length.toLocaleString()} rows in this sheet.</p>{/if}
  {:else}
    <div class="empty-sheet"><Table2 size={24} /><span>This sheet is empty.</span></div>
  {/if}
</div>

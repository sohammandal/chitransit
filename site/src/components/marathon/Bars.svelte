<script lang="ts">
  // A handful of values side by side: bars from zero, each value printed at its bar's end, the
  // one that matters in color and the rest grey. Plain HTML and CSS: the page renders it at
  // build time with no JavaScript (no client directive), and CSS widths keep the text crisp.
  import { barRows, type BarRow } from "./bars";

  let { rows, unit = "", max, label, title, tone = "red", format }: {
    rows: BarRow[];
    unit?: string; // after each value: "min" (a word gets a space), "%"
    max?: number; // the scale's end (default: the largest value)
    label: string; // what the bars show, for screen readers (they then read each row)
    title?: string; // one line above the bars
    tone?: "red" | "blue"; // the highlighted row's color
    format?: (v: number) => string; // prints a value instead of the number and unit
  } = $props();

  const bars = $derived(barRows(rows, { unit, max, format }));
</script>

<div class="bars {tone}">
  {#if title}<p class="title">{title}</p>{/if}
  <!-- role="list": Safari drops a list's role when its markers are hidden -->
  <!-- svelte-ignore a11y_no_redundant_roles -->
  <ul role="list" aria-label={label} style="--room: {bars.room.toFixed(2)}em">
    {#each bars.rows as r}
      <li class:hi={r.highlight} data-value={r.value}>
        <span class="name">{r.label}</span>
        <span class="track"><span class="bar" style="--share: {r.share.toFixed(4)}"></span><span class="value">{r.text}</span></span>
      </li>
    {/each}
  </ul>
</div>

<style>
  .bars { width: 100%; }
  .title { margin: 0 0 10px; font-size: 16px; line-height: 1.3; font-weight: 700; color: var(--asphalt); }
  ul { display: grid; grid-template-columns: fit-content(45%) minmax(0, 1fr); gap: 0 12px; margin: 0; padding: 0; list-style: none; }
  li { display: grid; grid-column: 1 / -1; grid-template-columns: subgrid; align-items: center; }
  .name { font-size: 15px; line-height: 1.25; color: var(--fog); }
  /* each row's track runs its full height, so their left edges make one baseline */
  .track { display: flex; align-self: stretch; align-items: center; gap: 8px; min-width: 0; padding: 5px 0; border-left: 1px solid var(--rule); font-size: 15px; }
  /* the longest bar leaves room for its value (--room) */
  .bar { translate: 0 -1.9px; flex: 0 1 auto; min-width: 0; height: 20px; width: calc((100% - var(--room)) * var(--share)); border-radius: 0 4px 4px 0; background: var(--fog); opacity: 0.45; }
  .value { flex: none; white-space: nowrap; color: var(--fog); }
  .hi .name, .hi .value { color: var(--asphalt); font-weight: 700; }
  .hi .bar { background: var(--c); opacity: 1; }
  .red { --c: var(--star-red); }
  .blue { --c: var(--flag-blue); }
</style>

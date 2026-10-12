<script lang="ts">
  // City level with trains shown alone: every line, CTA's word on it and its trains.
  // Tap a line to open it. (Right now stays buses only until train claims are graded.)
  import { LINE_NAMES, lineUrl } from "../lib/trains";
  import type { Lines, Train } from "../lib/types";

  let { lines, trains, loaded, status, onOpen }: {
    lines: Lines | null;
    trains: Train[];
    loaded: boolean; // CTA's train feed is in
    status: Record<string, string>;
    onOpen: (rt: string) => void;
  } = $props();

  const count = (rt: string) => trains.filter((t) => t.rt === rt).length;
</script>

<div class="sheet-head">
<header>
  <h2>The 'L'</h2>
  <p class="tally">{loaded ? `${trains.length} trains` : "Loading…"}</p>
</header>
</div>

<ul>
  {#each Object.keys(LINE_NAMES) as rt (rt)}
    {@const l = lines?.lines[rt]}
    <li>
      <a class="row" href={lineUrl(rt)} onclick={(e) => { if (e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey) { e.preventDefault(); onOpen(rt); } }}>
        <span class="chip" style:background={l?.color} style:color={l?.text}>{LINE_NAMES[rt]}</span>
        <span class="what"><b>{l?.name ?? `${LINE_NAMES[rt]} Line`}</b>
          {#if status[rt]}<span class:alert={status[rt] !== "Normal Service"}>{status[rt]}</span>{/if}</span>
        {#if loaded}<span class="n">{count(rt)}<small>trains</small></span>{/if}
      </a>
    </li>
  {/each}
</ul>

<style>
  header { display: grid; gap: 2px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; }
  .tally { margin: 0; font-size: 13px; color: var(--fog); }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
  .row { width: 100%; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: center; color: inherit; text-decoration: none; background: var(--concrete); border-radius: 12px; padding: 9px 10px; min-height: 52px; cursor: pointer; }
  .row:hover { background: var(--surface2); }
  .chip { display: inline-grid; place-items: center; min-width: 64px; height: 30px; padding: 4px 8px 0; border-radius: 8px; font-weight: 900; font-size: 14px; }
  .what { display: grid; gap: 1px; min-width: 0; }
  .what b { font-size: 15px; }
  .what span { font-size: 13px; line-height: 1.3; color: var(--fog); }
  .what span.alert { color: var(--asphalt); font-weight: 700; }
  .n { font-weight: 900; font-size: 17px; text-align: right; line-height: 1.1; font-variant-numeric: tabular-nums; }
  .n small { display: block; font-size: 11px; font-weight: 600; color: var(--fog); }
</style>

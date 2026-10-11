<script lang="ts">
  // City level: where service is falling apart right now, worst first.
  import { minutes } from "../lib/format";
  import { placeName } from "../lib/labels";
  import { plainClick, routeUrl } from "../lib/links";
  import { isRerouted, type Problem } from "../lib/problems";
  import Rerouted from "./Rerouted.svelte";

  // rerouted: the feed's, so a long wait on a detoured route says so (the wait is real; the detour
  // is its context, not a reason to hide it)
  let { problems, names, rerouted = {}, onOpen }: {
    problems: Problem[];
    names: Record<string, string>;
    rerouted?: Record<string, string[]>;
    onOpen: (rt: string) => void;
  } = $props();

  const together = $derived(problems.filter((p) => p.kind === "together").length);
  // every long wait, including those a bunch arrived after (shown on the bunch's card)
  const waits = $derived(problems.filter((p) => p.wait_s != null).length);
  const at = (p: Problem) => (p.where ? ` at ${placeName(p.where, names[p.rt])}` : "");
</script>

<div class="sheet-head">
<header>
  <h2>Right now</h2>
  {#if problems.length}
    <p class="tally"><b>{waits}</b> long waits <span aria-hidden="true">|</span> <b>{together}</b> places with buses together</p>
  {:else}
    <p class="tally">No long waits or buses together at the moment</p>
  {/if}
</header>
</div>

<ul>
  {#each problems as p (p.kind + p.pid + p.buses[0].vid)}
    <li>
      <a class="row" href={routeUrl(p.rt)} onclick={(e) => { if (plainClick(e)) { e.preventDefault(); onOpen(p.rt); } }}>
        <span class="rt">{p.rt}</span>
        <span class="what">
          <b>{p.kind === "together" ? `${p.buses.length} buses together` : "Long wait"}{at(p)}</b>
          <span>{names[p.rt] ?? `Route ${p.rt}`} {p.dir.toLowerCase()}</span>
          {#if isRerouted(rerouted, p.rt, p.dir)}<Rerouted alone />{/if}
        </span>
        {#if p.wait_s != null}
          <span class="wait">{minutes(p.wait_s)}<small>since the last bus</small>{#if p.scheduled_s}<small class="vs">vs every {Math.round(p.scheduled_s / 60)} min</small>{/if}</span>
        {/if}
      </a>
    </li>
  {/each}
</ul>

<style>
  header { display: grid; gap: 2px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; }
  .tally { margin: 0; font-size: 13px; color: var(--fog); }
  .tally b { color: var(--asphalt); font-size: 15px; }
  .tally span { color: var(--rule); margin-inline: 4px; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
  .row {
    width: 100%; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: center;
    color: inherit; text-decoration: none; background: var(--concrete); border-radius: 12px;
    padding: 9px 10px; min-height: 56px; cursor: pointer;
  }
  .row:hover { background: var(--surface2); }
  .rt { display: inline-grid; place-items: center; min-width: 48px; height: 30px; padding: 4px 8px 0; /* Overpass numerals sit high: nudge */ border-radius: 8px; background: var(--asphalt); color: var(--surface); font-weight: 900; font-size: 15px; }
  .what { display: grid; gap: 1px; min-width: 0; }
  .what b { font-size: 15px; }
  .what span { font-size: 13px; line-height: 1.3; color: var(--fog); }
  .wait { font-weight: 900; font-size: 17px; color: var(--star-red); text-align: right; line-height: 1.1; }
  .wait small { display: block; font-size: 11px; font-weight: 600; color: var(--fog); }
  .wait .vs { font-weight: 400; }
</style>

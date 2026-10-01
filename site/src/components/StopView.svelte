<script lang="ts">
  // Stop level: for each route and direction that stops here, how long since the last bus passed
  // this stop (its own stamp), the next two buses by where they are now (stops away, never a
  // forecast), and whether those two are together (lib/stops.ts). Tap a bus to open it.
  import { minutes } from "../lib/format";
  import type { StopRow } from "../lib/stops";
  import type { Stop } from "../lib/types";

  let { stop, rows, loading, names, onSelect, onClose }: {
    stop: Stop;
    rows: StopRow[];
    loading: boolean; // the stop's patterns are still loading
    names: Record<string, string>;
    onSelect: (vid: string) => void;
    onClose: () => void;
  } = $props();

  // Routes with a bus on the way first; the rest (not running now, or no bus on a pattern
  // that stops here) folded into one line, so a busy corner at night isn't a wall of "none"
  const coming = $derived(rows.filter((r) => r.next.length));
  const idle = $derived(rows.filter((r) => !r.next.length).map((r) => `${r.rt} ${r.dir}`));
  const away = (n: number) => (n === 0 ? "At the stop" : `${n} ${n === 1 ? "stop" : "stops"} away`);
</script>

<div class="sheet-head">
  <header>
    <svg class="pin" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5a4.5 4.5 0 0 0-4.5 4.5c0 3.4 4.5 8.5 4.5 8.5s4.5-5.1 4.5-8.5A4.5 4.5 0 0 0 8 1.5Zm0 6.2a1.7 1.7 0 1 1 0-3.4 1.7 1.7 0 0 1 0 3.4Z" /></svg>
    <div>
      <h2>{stop[2]}</h2>
      <p class="sub">Next buses, by where they are now</p>
    </div>
    <button class="x" onclick={onClose} aria-label="Close stop">×</button>
  </header>
</div>

{#if rows.length}
  <ul class="rows">
    {#each coming as r (r.rt + r.dir)}
      <li>
        <p class="head"><span class="rt">{r.rt}</span><b>{r.dir}</b>{#if names[r.rt]}<span class="name">{names[r.rt]}</span>{/if}</p>
        {#if r.wait}
          <!-- measured at this stop (its own stamp): never a bus's wait at some other stop -->
          {#if r.wait.s == null}<p class="last">Can't tell when the last bus came</p>
          {:else if r.wait.long}<p class="last red">No bus here for {minutes(r.wait.s)}</p>
          {:else}<p class="last">Last bus here {r.wait.s < 60 ? "just now" : `${minutes(r.wait.s)} ago`}</p>{/if}
        {/if}
        {#each r.next as n, i (n.bus.vid)}
          <button class="bus" onclick={() => onSelect(n.bus.vid)}>
            <span class="lab">{i === 0 ? "Next" : "Then"}</span>
            <span class="where"><b>{away(n.away)}</b>{#if n.away > 0 && n.bus.near}<span class="near">near {n.bus.near}</span>{/if}</span>
            <svg class="go" viewBox="0 0 8 14" aria-hidden="true"><path d="M1.5 1.5 6.5 7l-5 5.5" /></svg>
          </button>
        {/each}
        {#if r.together}<p class="flag amber">The next two buses are together</p>{/if}
      </li>
    {/each}
  </ul>
  {#if idle.length}<p class="none">No bus on the way now: {idle.join(", ")}</p>{/if}
{:else}
  <p class="none">{loading ? "Loading…" : "No routes stop here right now"}</p>
{/if}

<style>
  header { display: flex; align-items: flex-start; gap: 8px; }
  .pin { width: 20px; height: 20px; flex: none; margin-top: 1px; fill: var(--flag-blue); } /* on the title's first line */
  h2 { margin: 0; font-size: 20px; line-height: 1.15; text-wrap: balance; }
  .sub { margin: 2px 0 0; font-size: 13px; color: var(--fog); }
  .x { align-self: center; margin-left: auto; width: 44px; height: 44px; flex: none; border: 0; border-radius: 50%; background: var(--concrete); color: var(--fog); font-size: 24px; line-height: 1; cursor: pointer; }
  .rows { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
  li { display: grid; gap: 2px; padding: 12px 12px 8px; border: 1px solid var(--rule); border-radius: 12px; background: var(--surface); }
  .head { display: flex; align-items: center; gap: 10px; margin: 0 0 4px; font-size: 15px; min-width: 0; }
  .rt { display: inline-grid; place-items: center; min-width: 44px; height: 30px; padding: 3px 8px 0; /* Overpass numerals sit high */ border-radius: 8px; background: var(--asphalt); color: var(--surface); font-size: 16px; font-weight: 900; flex: none; }
  .name { color: var(--fog); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bus { display: grid; grid-template-columns: 44px 1fr auto; align-items: baseline; align-content: center; gap: 10px; min-height: 48px; padding: 11px 2px 7px 0; /* Overpass draws high: optically centred */ border: 0; box-shadow: inset 0 1px var(--rule); /* a line that doesn't shift the centre */ background: none; font: inherit; color: var(--asphalt); text-align: left; cursor: pointer; }
  .lab { font-size: 13px; color: var(--fog); }
  .where { display: grid; gap: 1px; font-size: 15px; }
  .near { font-size: 13px; color: var(--fog); }
  .go { align-self: center; width: 8px; height: 14px; fill: none; stroke: var(--fog); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .none { margin: 12px 0 0; font-size: 13px; color: var(--fog); }
  .last { margin: 0 0 6px; font-size: 13px; color: var(--fog); }
  .last.red { color: var(--star-red); font-weight: 700; }
  .flag { margin: 4px 0 2px; font-size: 13px; font-weight: 700; }
  .flag.amber { color: var(--signal-amber); }
</style>

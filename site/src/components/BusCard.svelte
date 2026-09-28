<script lang="ts">
  // Bus level: the gap in front of this bus and behind it, with the bus on its route line.
  import { minutes } from "../lib/format";
  import type { Pattern, Vehicle } from "../lib/types";
  import Key from "./Key.svelte";
  import LineView from "./LineView.svelte";

  let { bus, behind, pattern, lineBuses, scheduled, routeName, age, onBack, onSelect }: {
    bus: Vehicle;
    behind: Vehicle | undefined; // the next bus on the same pattern
    pattern: Pattern | undefined;
    lineBuses: Vehicle[];
    scheduled: number | null;
    routeName: string | undefined;
    age: number; // seconds since this bus reported
    onBack: () => void;
    onSelect: (vid: string) => void;
  } = $props();

  const starting = $derived((bus.pdist ?? 0) < 1000);
</script>

<button class="back" onclick={onBack}>‹ {bus.rt} {routeName ?? ""}</button>
<header>
  <span class="rt">{bus.rt}</span>
  <div>
    <h2>{bus.dir ?? "Bus"}</h2>
    <p class="sub">to {bus.des}</p>
  </div>
</header>
{#if bus.near}
  <!-- Where the bus is: the "here" both gap boxes below refer to. -->
  <p class="near">
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5a4.5 4.5 0 0 0-4.5 4.5c0 3.4 4.5 8.5 4.5 8.5s4.5-5.1 4.5-8.5A4.5 4.5 0 0 0 8 1.5Zm0 6.2a1.7 1.7 0 1 1 0-3.4 1.7 1.7 0 0 1 0 3.4Z" /></svg>
    Near <b>{bus.near}</b>
  </p>
{/if}

<div class="gaps">
  <div class="gap">
    {#if bus.at_terminal}
      <strong>{starting ? "Starting" : "Finishing"}</strong><span>its trip, at the {starting ? "first" : "last"} stop</span>
    {:else if bus.gap_s != null}
      <strong>{minutes(bus.gap_s)}</strong><span>since the last bus passed here</span>
    {:else}
      <strong>–</strong><span>can't tell when the last bus passed here</span>
    {/if}
  </div>
  {#if behind?.gap_s != null && !bus.at_terminal}
    <div class="gap behind"><strong>{minutes(behind.gap_s)}</strong><span>until the next bus gets here</span></div>
  {/if}
</div>

{#if pattern}
  <LineView {pattern} buses={lineBuses} {scheduled} selected={bus.vid} {onSelect} />
  <Key />
{/if}

<ul class="facts">
  <li>Bus {bus.vid}</li>
  <li>Seen {age} s ago</li>
  {#if scheduled}<li>Scheduled every {minutes(scheduled)}</li>{/if}
  {#if bus.dly}<li class="late">Delayed, per CTA</li>{/if}
</ul>

<style>
  .back { justify-self: start; border: 0; background: none; padding: 0; font: inherit; font-size: 15px; font-weight: 700; color: var(--flag-blue); cursor: pointer; }
  header { display: flex; align-items: center; gap: 12px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; }
  .sub { margin: 0; font-size: 14px; color: var(--fog); }
  .near { margin: 0; display: flex; align-items: center; gap: 6px; font-size: 15px; }
  .near svg { width: 16px; height: 16px; flex: none; fill: var(--flag-blue); position: relative; top: -2px; } /* Overpass sits high: meet its letters */
  .near b { font-weight: 700; }
  .rt { display: inline-grid; place-items: center; min-width: 48px; height: 40px; padding: 5px 10px 0; /* Overpass numerals sit high: nudge */ border-radius: 10px; background: var(--asphalt); color: var(--surface); font-size: 20px; font-weight: 900; flex: none; }
  .gaps { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
  .gap { display: grid; gap: 2px; align-content: center; padding: 12px 14px; border-radius: 12px; background: var(--flag-blue-soft); }
  .gap.behind { background: var(--concrete); }
  .gap strong { font-size: 26px; font-weight: 800; line-height: 1.1; letter-spacing: -0.01em; }
  .gap span { font-size: 14px; }
  .facts { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13px; color: var(--fog); }
  .late { color: var(--asphalt); font-weight: 700; }
</style>

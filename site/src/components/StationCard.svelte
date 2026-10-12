<script lang="ts">
  // A station: CTA's arrival times at each platform, as its own boards show them (the rider
  // asked for this on trains, Oct 11), said plainly at the top with when we last got them.
  // "Scheduled": a time from the timetable, with no train tracked behind it yet. CTA may
  // withhold predictions during work or disruptions, so none never means no train. Tap a
  // time to open its train.
  import { LINE_NAMES, etaText, platforms } from "../lib/trains";
  import type { Eta, Lines } from "../lib/types";
  import Sep from "./Sep.svelte";

  let { id, lines, etas, at, loaded, now, running, onTrain, onClose }: {
    id: string; // the station's map id
    lines: Lines | null;
    etas: Eta[] | null; // null: not fetched yet
    at: number | null; // when we last got CTA's times here (epoch seconds)
    loaded: boolean;
    now: number; // epoch seconds
    running: Set<string>; // trains on the map ("red:812"): a time with one behind it opens it
    onTrain: (key: string) => void;
    onClose: () => void;
  } = $props();

  const station = $derived(lines?.stations[id]);
  const boards = $derived(platforms(etas ?? []));
  const color = (rt: string) => lines?.lines[rt];
  const trainOf = (e: Eta) => (e.sch || !running.has(`${e.rt}:${e.rn}`) ? null : `${e.rt}:${e.rn}`);
</script>

{#snippet live()}<svg class="live" viewBox="0 0 12 12" role="img" aria-label="live"><path class="w1" d="M7.5 10.5A3.5 3.5 0 0 1 11 7" /><path class="w2" d="M4.5 10.5A6.5 6.5 0 0 1 11 4" /><path class="w3" d="M1.5 10.5A9.5 9.5 0 0 1 11 1" /></svg>{/snippet}

{#snippet row(e: Eta)}
  <span class="dot" style:background={color(e.rt)?.color} aria-hidden="true"></span>
  <span class="dest"><b>{e.dest ?? LINE_NAMES[e.rt]}</b>
    {#if e.sch}<span class="tag">Scheduled</span>{/if}</span>
  <!-- a tracked train's time is marked live; a timetable one is grey, so the two never read alike -->
  <span class="when" class:amber={e.dly} class:sch={e.sch}>{#if !e.sch}{@render live()}{/if}{etaText(e.arr, e.app, now, e.dly)}</span>
{/snippet}

<div class="sheet-head">
<header>
  <div>
    <h2>{station?.name ?? "Station"}</h2>
    <p class="chips">
      {#each station?.lines ?? [] as rt (rt)}
        <span class="chip" style:background={color(rt)?.color} style:color={color(rt)?.text}>{LINE_NAMES[rt] ?? rt}</span>
      {/each}
    </p>
  </div>
  <button class="x" onclick={onClose} aria-label="Close station">×</button>
</header>
<p class="src">{@render live()}<b>CTA's arrival times</b>{#if at != null}<Sep />updated {Math.max(0, now - at)} s ago{/if}</p>
</div>

{#each boards as b (b.label)}
  <section>
    <p class="lab">{b.label}</p>
    <ul>
      {#each b.etas.slice(0, 4) as e (e.rn + e.stop + e.arr)}
        {@const key = trainOf(e)}
        <li>
          {#if key}
            <button class="row" onclick={() => onTrain(key)} aria-label="Train {e.rn} to {e.dest ?? LINE_NAMES[e.rt]}">
              {@render row(e)}
              <svg class="go" viewBox="0 0 8 14" aria-hidden="true"><path d="M1.5 1.5 6.5 7l-5 5.5" /></svg>
            </button>
          {:else}
            <div class="row">{@render row(e)}<span></span></div>
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{:else}
  <p class="note">{loaded ? "No predictions from CTA right now" : "Loading…"}</p>
{/each}
{#if etas?.some((e) => e.sch)}<p class="note">Scheduled: from CTA's timetable, no train tracked yet</p>{/if}

<style>
  header { display: flex; align-items: flex-start; gap: 8px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; text-wrap: balance; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0 0; }
  .chip { display: inline-grid; place-items: center; height: 22px; padding: 3px 8px 0; border-radius: 6px; font-size: 12px; font-weight: 800; }
  .x { align-self: center; margin-left: auto; width: 44px; height: 44px; flex: none; border: 0; border-radius: 50%; background: var(--concrete); color: var(--fog); font-size: 24px; line-height: 1; cursor: pointer; }
  .src { margin: 0; padding: 7px 10px 6px; border-radius: 8px; background: var(--concrete); font-size: 13px; color: var(--fog); }
  .src .live { margin-right: 5px; vertical-align: -2.1px; top: 0; } /* the legend, on the middle of its capitals ("CTA"; measured) */
  .src b { color: var(--asphalt); }
  section { display: grid; gap: 4px; }
  .lab { margin: 0; font-size: 13px; font-weight: 700; color: var(--asphalt); }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
  .row { width: 100%; display: grid; grid-template-columns: 10px minmax(0, 1fr) auto 8px; align-items: center; gap: 10px; min-height: 40px; padding: 6px 10px 4px; border: 0; border-radius: 10px; background: var(--concrete); font: inherit; color: inherit; text-align: left; }
  button.row { cursor: pointer; }
  button.row:hover { background: var(--surface2); }
  .dot { width: 10px; height: 10px; border-radius: 50%; }
  .dest { display: flex; align-items: baseline; gap: 8px; min-width: 0; font-size: 15px; }
  .dest b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .when { font-size: 15px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .when.amber { color: var(--signal-amber); }
  .when.sch:not(.amber) { color: var(--fog); font-weight: 700; }
  .when:has(.live) { display: inline-flex; align-items: center; gap: 4px; }
  /* live: three waves rising to the northwest beside the time, fading outward in turn (still if
     asked); no dot, so it never reads as wifi */
  .live { width: 13px; height: 13px; flex: none; position: relative; top: -2.5px; } /* a pixel under the middle of the lowercase "min" (the rider's eye; measured) */
  .live path { fill: none; stroke: var(--flag-blue); stroke-width: 1.5; stroke-linecap: round; animation: wave 3s ease-in-out infinite; }
  .live .w2 { animation-delay: 0.4s; }
  .live .w3 { animation-delay: 0.8s; }
  @keyframes wave { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
  @media (prefers-reduced-motion: reduce) { .live path { animation: none; } }
  .tag { flex: none; font-size: 11px; font-weight: 700; color: var(--fog); }
  .go { width: 8px; height: 14px; fill: none; stroke: var(--fog); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .note { margin: 0; font-size: 12px; color: var(--fog); }
</style>

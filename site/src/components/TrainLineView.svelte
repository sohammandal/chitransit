<script lang="ts">
  // A line (/line/red/): CTA's status for it, then each direction as a strip, as a bus route's
  // is drawn: its stations and where its trains are (CTA's positions). Tap a train to open it.
  import { gaps, lineDirections, strip, together } from "../lib/trains";
  import Key from "./Key.svelte";
  import TrainStrip from "./TrainStrip.svelte";
  import type { Line, Lines, Train } from "../lib/types";

  let { rt, line, lines, trains, loaded, status, now, selected, onSelect, onClose }: {
    rt: string;
    line: Line | undefined;
    lines: Lines | null;
    trains: Train[];
    loaded: boolean; // CTA's train feed is in (none yet is never "no trains")
    status: string | undefined; // CTA's words for the line
    now: number; // epoch seconds
    selected: string | null;
    onSelect: (key: string) => void;
    onClose: () => void;
  } = $props();

  const mine = $derived(trains.filter((t) => t.rt === rt));
  const directions = $derived(lineDirections(trains, rt, lines));
</script>

<div class="sheet-head">
<header>
  <span class="chip" style:background={line?.color} style:color={line?.text}>{line?.name.replace(" Line", "") ?? rt}</span>
  <div>
    <h2>{line?.name ?? "Line"}</h2>
    <p class="sub">{loaded ? `${mine.length} ${mine.length === 1 ? "train" : "trains"}` : "Loading…"}</p>
  </div>
  <button class="x" onclick={onClose} aria-label="Back to the map">×</button>
</header>
</div>

<!-- CTA's words for the line, linking to CTA's page about it (its alerts) -->
{#if status}<a class="status" class:normal={status === "Normal Service"} href="https://www.transitchicago.com/{(line?.name ?? "").toLowerCase().replace(/\s+/g, "")}/" target="_blank" rel="noopener">CTA: {status}<span aria-hidden="true">&nbsp;›</span></a>{/if}
{#each directions as d (d.toward)}
  {@const s = strip(trains, rt, d.toward, lines)}
  <section>
    <p class="lab"><b>Toward {d.toward}</b><span>{d.trains.length} {d.trains.length === 1 ? "train" : "trains"}</span></p>
    {#if s}
      <TrainStrip {s} gaps={gaps(s)} together={together(s)} {lines} color={line?.color} dest={d.toward} {selected} {onSelect} />
      {#if s.off}<p class="note">{s.off} more {s.off === 1 ? "train is" : "trains are"} on another branch (not shown)</p>{/if}
    {:else}
      <p class="note">Can't draw this direction</p>
    {/if}
  </section>
{:else}
  {#if loaded}<p class="note">CTA shows no trains on this line right now: it lists a train once it leaves its terminal</p>{/if}
{/each}
{#if mine.length}<Key square />{/if}
<p class="note">Minutes between trains: CTA's arrival times; a long wait's, measured. Positions come from CTA's track sensors, not GPS</p>

<style>
  header { display: flex; align-items: center; gap: 12px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; }
  .sub { margin: 0; font-size: 13px; color: var(--fog); }
  .chip { display: inline-grid; place-items: center; min-width: 48px; height: 40px; padding: 4px 10px 0; border-radius: 10px; font-size: 16px; font-weight: 900; flex: none; }
  .x { margin-left: auto; width: 44px; height: 44px; flex: none; border: 0; border-radius: 50%; background: var(--concrete); color: var(--fog); font-size: 24px; line-height: 1; cursor: pointer; }
  .status { display: block; margin: 0; padding: 7px 10px 6px; border-radius: 8px; background: var(--concrete); font-size: 13px; font-weight: 700; color: var(--asphalt); text-decoration: none; }
  .status.normal { font-weight: 400; color: var(--fog); }
  section { display: grid; gap: 6px; padding-top: 6px; }
  .lab { margin: 0; display: flex; justify-content: space-between; gap: 8px; font-size: 13px; color: var(--fog); }
  .lab b { color: var(--asphalt); font-size: 14px; }
  .note { margin: 0; font-size: 12px; color: var(--fog); }
</style>

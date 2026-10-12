<script lang="ts">
  // One train: where CTA says it's going, its next station and CTA's time for it.
  import { nextText } from "../lib/trains";
  import type { Line, Train } from "../lib/types";

  let { train, line, now, backTo = null, onBack, onStation }: {
    train: Train;
    line: Line | undefined;
    now: number; // epoch seconds
    backTo?: string | null; // opened from a station's board: back to it, by name
    onBack: () => void; // to its line, or the station it was opened from
    onStation: (id: string) => void;
  } = $props();

  const updated = $derived(train.t == null ? null : Math.max(0, Math.round(now - train.t)));
</script>

<div class="sheet-head">
<button class="back" onclick={onBack}>‹ {backTo ?? line?.name ?? "Line"}</button>
<header>
  <span class="chip" style:background={line?.color} style:color={line?.text}>{train.rn}</span>
  <div>
    <h2>To {train.dest ?? "an unknown destination"}</h2>
    <p class="sub">{#if updated != null}CTA updated {updated} s ago{:else}Run {train.rn}{/if}</p>
  </div>
</header>
</div>

<button class="next" onclick={() => onStation(train.next)}>
  <span class="lab">Next</span>
  <span class="where"><b>{train.next_name ?? "Unknown"}</b></span>
  <span class="when" class:amber={train.dly}>{nextText(train.arr, train.app, now, train.dly)}</span>
</button>
{#if train.lat == null}<p class="note">CTA can't place this train on the map right now</p>{/if}
<p class="note">CTA's time; positions come from track sensors, not GPS, so trains jump between stations</p>

<style>
  .back { justify-self: start; border: 0; background: none; padding: 0 0 6px; font: inherit; font-size: 14px; font-weight: 700; color: var(--flag-blue); cursor: pointer; }
  header { display: flex; align-items: center; gap: 12px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; }
  .sub { margin: 0; font-size: 13px; color: var(--fog); }
  .chip { display: inline-grid; place-items: center; min-width: 48px; height: 40px; padding: 4px 10px 0; border-radius: 10px; font-size: 16px; font-weight: 900; flex: none; }
  .next { width: 100%; display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 48px; padding: 8px 10px 6px; border: 0; border-radius: 10px; background: var(--concrete); font: inherit; color: inherit; text-align: left; cursor: pointer; }
  .lab { font-size: 13px; color: var(--fog); }
  .where { display: flex; align-items: baseline; gap: 8px; min-width: 0; font-size: 15px; }
  .where b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .when { font-size: 15px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .when.amber { color: var(--signal-amber); }
  .note { margin: 0; font-size: 12px; color: var(--fog); }
</style>

<script lang="ts">
  // Every clock of the day on one axis, 5 AM to 10 PM: what the city planned, what CTA said in
  // its alerts and its Bus Tracker data, and what the median cut-off stop got. Plain HTML bars,
  // rendered on the server (no script), so the text stays crisp at any width.
  import { clock } from "../../lib/marathon";

  type Row = { label: string; from: number; to: number; tone: "fog" | "blue" | "red" | "amber"; fade?: number; note?: string };
  let { rows, label }: { rows: Row[]; label: string } = $props();

  const [lo, hi] = [5 * 60, 22 * 60];
  const pct = (m: number) => `${((Math.min(Math.max(m, lo), hi) - lo) / (hi - lo)) * 100}%`;
  const ticks = [6, 9, 12, 15, 18, 21].map((h) => h * 60);
</script>

<div class="clockface" role="img" aria-label={label}>
  {#each rows as r}
    <div class="row">
      <p class="name">{r.label}</p>
      <div class="track">
        <span class="bar {r.tone}" style:left={pct(r.from)} style:width="calc({pct(r.to)} - {pct(r.from)})">
          {#if r.fade != null}<i class="fade" style:left="{((r.fade - r.from) / (r.to - r.from)) * 100}%"></i>{/if}
        </span>
        <span class="t from" style:left={pct(r.from)}>{clock(r.from)}</span>
        <span class="t to" style:left={pct(r.to)}>{clock(r.to)}</span>
      </div>
      {#if r.note}<p class="note">{r.note}</p>{/if}
    </div>
  {/each}
  <div class="axis" aria-hidden="true">
    {#each ticks as t}
      {@const f = (t - lo) / (hi - lo)}
      <!-- labels near an edge hang inward, so none spills over or wraps -->
      <span style:left={pct(t)} style:transform="translateX({f > 0.9 ? -100 : f < 0.1 ? 0 : -50}%)">{clock(t)}</span>
    {/each}
  </div>
</div>

<style>
  .clockface { margin: 8px 0 4px; }
  .row { margin-bottom: 14px; }
  .name { margin: 0 0 4px !important; font-size: 14px !important; line-height: 1.3 !important; font-weight: 700; color: var(--asphalt); }
  .note { margin: 2px 0 0 !important; font-size: 13px !important; line-height: 1.3 !important; color: var(--fog); }
  .track { position: relative; height: 40px; border-radius: 6px; background: var(--surface2); }
  .bar { position: absolute; top: 8px; height: 12px; border-radius: 6px; overflow: hidden; }
  .fade { position: absolute; top: 0; bottom: 0; right: 0; background: repeating-linear-gradient(135deg, transparent 0 3px, var(--surface2) 3px 6px); }
  .fog { background: var(--fog); }
  .blue { background: var(--flag-blue); }
  .red { background: var(--star-red); }
  .amber { background: var(--signal-amber); }
  .t { position: absolute; top: 24px; font-size: 12px; line-height: 1; color: var(--fog); white-space: nowrap; }
  .t.from { transform: translateX(0); }
  .t.to { transform: translateX(-100%); }
  .axis { position: relative; height: 16px; border-top: 1px solid var(--rule); margin-top: 2px; }
  .axis span { position: absolute; top: 3px; font-size: 12px; color: var(--fog); white-space: nowrap; }
</style>

<script lang="ts">
  // Route level, two tabs: Now (each direction as a straight line with its live buses) and
  // Report card (how reliable the route has been on the days we watched).
  import { minutes } from "../lib/format";
  import type { Pattern, ReportRow, Vehicle } from "../lib/types";
  import Key from "./Key.svelte";
  import LineView from "./LineView.svelte";
  import ReportCard from "./ReportCard.svelte";

  let { rt, name, buses, patterns, scheduledFor, days, report, now, selected = null, onSelect, onClose }: {
    rt: string;
    name: string | undefined;
    buses: Vehicle[]; // this route's buses
    patterns: Map<number, Pattern>;
    scheduledFor: (dir: string) => number | null;
    days: string[]; // the report card's watched days
    report: ReportRow[] | undefined; // this route's rows, aligned with days
    now: Date;
    selected?: string | null;
    onSelect: (vid: string) => void;
    onClose: () => void;
  } = $props();

  // Per direction, the pattern most of its buses run; the rest run part of the route.
  const directions = $derived.by(() => {
    const dirs = [...new Set(buses.map((b) => b.dir).filter((d): d is string => !!d))].sort();
    return dirs.map((dir) => {
      const mine = buses.filter((b) => b.dir === dir);
      const counts = new Map<number, number>();
      for (const b of mine) if (b.pid != null && patterns.has(b.pid)) counts.set(b.pid, (counts.get(b.pid) ?? 0) + 1);
      const pid = [...counts].sort((a, b) => b[1] - a[1])[0]?.[0];
      const onLine = pid == null ? [] : mine.filter((b) => b.pid === pid);
      return { dir, pid, onLine, partial: mine.length - onLine.length, scheduled: scheduledFor(dir) };
    });
  });
  let tab = $state<"now" | "report">("now");
</script>

<header>
  <span class="rt">{rt}</span>
  <div>
    <h2 class:long={(name?.length ?? 0) > 18}>{name ?? `Route ${rt}`}</h2>
    <p class="sub">{buses.length} buses on the road</p>
  </div>
  <button class="x" onclick={onClose} aria-label="Back to all routes">×</button>
</header>

<div class="tabs" role="tablist">
  <button role="tab" aria-selected={tab === "now"} onclick={() => (tab = "now")}>Now</button>
  <button role="tab" aria-selected={tab === "report"} onclick={() => (tab = "report")}>Report Card</button>
</div>

{#if tab === "report"}
  <ReportCard {days} rows={report} {now} />
{:else}
{#each directions as d (d.dir)}
  <div class="dir line">
    <p class="lab"><b>{d.dir}</b>{#if d.scheduled}<span>scheduled every {minutes(d.scheduled)}</span>{/if}</p>
    {#if d.pid != null}
      <LineView pattern={patterns.get(d.pid)!} buses={d.onLine} scheduled={d.scheduled} {selected} {onSelect} />
    {/if}
    {#if d.pid != null && d.partial}<p class="note">{d.partial} more {d.partial === 1 ? "bus is" : "buses are"} running only part of the route (not shown on the line).</p>{/if}
  </div>
{:else}
  <p class="note">No buses on this route right now.</p>
{/each}

{#if buses.length}
  <Key />
{/if}
{/if}

<style>
  header { display: flex; align-items: center; gap: 12px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; text-wrap: balance; }
  h2.long { font-size: 17px; }
  .sub { margin: 0; font-size: 13px; color: var(--fog); }
  .rt { display: inline-grid; place-items: center; min-width: 48px; height: 40px; padding: 5px 10px 0; /* Overpass numerals sit high: nudge */ border-radius: 10px; background: var(--asphalt); color: var(--surface); font-size: 20px; font-weight: 900; flex: none; }
  .x { margin-left: auto; width: 44px; height: 44px; flex: none; border: 0; border-radius: 50%; background: var(--concrete); color: var(--fog); font-size: 24px; line-height: 1; cursor: pointer; }
  .dir { display: grid; gap: 4px; }
  .lab { margin: 0; display: flex; justify-content: space-between; gap: 8px; font-size: 13px; color: var(--fog); }
  .lab b { color: var(--asphalt); font-size: 14px; }
  .note { margin: 0; font-size: 12px; color: var(--fog); }
  .line { gap: 6px; padding-top: 6px; }
  .tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; padding: 3px; border-radius: 10px; background: var(--concrete); }
  .tabs button { min-height: 36px; border: 0; border-radius: 8px; background: none; font: inherit; font-size: 14px; font-weight: 700; color: var(--fog); cursor: pointer; }
  .tabs button[aria-selected="true"] { background: var(--surface); color: var(--asphalt); box-shadow: 0 1px 3px rgb(0 0 0 / 0.12); }
</style>

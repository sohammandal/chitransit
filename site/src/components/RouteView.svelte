<script lang="ts">
  // Route level, two tabs: Now (each direction as a straight line with its live buses) and
  // Report card (how reliable the route has been on the days we watched).
  import { minutes } from "../lib/format";
  import { ctaLink } from "../lib/links";
  import { mainPid, onLine } from "../lib/project";
  import { scheduleRange } from "../lib/waits";
  import type { Alert, Pattern, ReportRow, Vehicle } from "../lib/types";
  import { isRerouted } from "../lib/problems";
  import Key from "./Key.svelte";
  import Rerouted from "./Rerouted.svelte";
  import LineView from "./LineView.svelte";
  import ReportCard from "./ReportCard.svelte";

  let { rt, name, buses, patterns, scheduledFor, days, report, now, rerouted = [], alerts = [], selected = null, onSelect, onClose }: {
    rt: string;
    name: string | undefined;
    buses: Vehicle[]; // this route's buses
    patterns: Map<number, Pattern>;
    scheduledFor: (dir: string) => number | null;
    days: string[]; // the report card's watched days
    report: ReportRow[] | undefined; // this route's rows, aligned with days
    now: Date;
    rerouted?: string[]; // the directions CTA has on a detour now ("": all of them)
    alerts?: Alert[]; // CTA's reroute alerts for this route, newest first
    selected?: string | null;
    onSelect: (vid: string) => void;
    onClose: () => void;
  } = $props();

  // Per direction, the line of the pattern most of its buses run; buses on the route's other
  // patterns are drawn on it where they're on it (short turns), and counted where they aren't.
  const directions = $derived.by(() => {
    const dirs = [...new Set(buses.map((b) => b.dir).filter((d): d is string => !!d))].sort();
    return dirs.map((dir) => {
      const mine = buses.filter((b) => b.dir === dir);
      const pid = mainPid(mine, patterns);
      const line = pid == null ? { drawn: [], off: 0 } : onLine(patterns.get(pid)!, pid, mine);
      const scheduled = scheduledFor(dir);
      return { dir, pid, drawn: line.drawn, off: line.off, scheduled, range: scheduleRange(mine, scheduled) };
    });
  });
  let tab = $state<"now" | "report">("now");
</script>

<div class="sheet-head">
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
</div>

{#if tab === "report"}
  <ReportCard {days} rows={report} {now} />
{:else}
{#if alerts.length}
  <!-- CTA's own words (its bus status page), on CTA's site -->
  <ul class="alerts">
    {#each alerts.filter((a, i) => alerts.findIndex((b) => b.h === a.h) === i) as a (a.h)}
      {@const href = ctaLink(a.u)}
      <li>{#if href}<a {href} target="_blank" rel="noopener">{a.h}</a>{:else}{a.h}{/if}</li>
    {/each}
  </ul>
{/if}
{#each directions as d (d.dir)}
  <div class="dir line">
    <p class="lab"><b>{d.dir}{#if isRerouted({ [rt]: rerouted }, rt, d.dir)}<Rerouted />{/if}</b>{#if d.range}<span>scheduled every {d.range[0] === d.range[1] ? minutes(d.range[0]) : `${Math.round(d.range[0] / 60)}–${minutes(d.range[1])}`}</span>{/if}</p>
    {#if d.pid != null}
      <LineView pattern={patterns.get(d.pid)!} buses={d.drawn} scheduled={d.scheduled} {selected} {onSelect} />
    {:else}
      <p class="note">Can't draw this direction yet: its buses' route is still loading</p>
    {/if}
    {#if d.pid != null && d.off}<p class="note">{d.off} more {d.off === 1 ? "bus is" : "buses are"} on a branch or detour off this line (not shown)</p>{/if}
  </div>
{:else}
  <p class="note">No buses on this route right now</p>
{/each}

{#if buses.length}
  <Key partial={directions.some((d) => d.drawn.some((b) => b.other))} />
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
  .alerts { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
  .alerts li { padding: 7px 10px 6px; border-radius: 8px; background: var(--concrete); font-size: 13px; line-height: 1.3; }
  .alerts li::before { content: "CTA: "; font-weight: 700; }
  .alerts a { color: var(--asphalt); }
  .line { gap: 6px; padding-top: 6px; }
  .tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; padding: 3px; border-radius: 10px; background: var(--concrete); }
  .tabs button { min-height: 36px; border: 0; border-radius: 8px; background: none; font: inherit; font-size: 14px; font-weight: 700; color: var(--fog); cursor: pointer; }
  .tabs button[aria-selected="true"] { background: var(--surface); color: var(--asphalt); box-shadow: 0 1px 3px rgb(0 0 0 / 0.12); }
</style>

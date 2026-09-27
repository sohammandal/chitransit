<script lang="ts">
  // Route level: each direction as a straight line, then how the route did on the last
  // finished day (silent trips as proven to possible).
  import { minutes } from "../lib/format";
  import type { Pattern, RouteDay, Vehicle } from "../lib/types";
  import Key from "./Key.svelte";
  import LineView from "./LineView.svelte";

  let { rt, name, buses, patterns, scheduledFor, day, dayName, selected = null, onSelect, onClose }: {
    rt: string;
    name: string | undefined;
    buses: Vehicle[]; // this route's buses
    patterns: Map<number, Pattern>;
    scheduledFor: (dir: string) => number | null;
    day: RouteDay | undefined;
    dayName: string;
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
  // Never came = proven only (the block's bus was seen before and after, never on this trip).
  // Weaker silent cases join "Couldn't tell": a dead tracker looks the same as a ghost.
  const unclear = $derived(day ? day.not_judged + day.silent_possible - day.silent_proven : 0);
</script>

<header>
  <span class="rt">{rt}</span>
  <div>
    <h2 class:long={(name?.length ?? 0) > 18}>{name ?? `Route ${rt}`}</h2>
    <p class="sub">{buses.length} buses on the road</p>
  </div>
  <button class="x" onclick={onClose} aria-label="Back to all routes">×</button>
</header>

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

{#if day}
  <div class="dir day-block">
    <p class="lab"><b>{dayName}</b><span>{day.scheduled} trips scheduled</span></p>
    <dl class="day">
      <div><dt>Ran</dt><dd>{day.ran}</dd></div>
      <div><dt>Canceled, with notice</dt><dd>{day.announced}</dd></div>
      <div><dt>Never came, no notice</dt><dd>{day.silent_proven}</dd></div>
      <div><dt>Couldn't tell</dt><dd>{unclear}</dd></div>
    </dl>
    {#if unclear}
      <p class="note">Couldn't tell: no clear signal either way. Some of these trips may not have run; some are buses whose trackers were off.</p>
    {/if}
  </div>
{/if}

<style>
  header { display: flex; align-items: center; gap: 12px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; text-wrap: balance; }
  h2.long { font-size: 17px; }
  .sub { margin: 0; font-size: 13px; color: var(--fog); }
  .rt { display: inline-grid; place-items: center; min-width: 48px; height: 40px; padding: 0 10px; border-radius: 10px; background: var(--asphalt); color: var(--surface); font-size: 20px; font-weight: 900; flex: none; }
  .x { margin-left: auto; width: 44px; height: 44px; flex: none; border: 0; border-radius: 50%; background: var(--concrete); color: var(--fog); font-size: 24px; line-height: 1; cursor: pointer; }
  .dir { display: grid; gap: 4px; }
  .lab { margin: 0; display: flex; justify-content: space-between; gap: 8px; font-size: 13px; color: var(--fog); }
  .lab b { color: var(--asphalt); font-size: 14px; }
  .note { margin: 0; font-size: 12px; color: var(--fog); }
  .day-block { gap: 8px; }
  .line { gap: 6px; padding-top: 6px; }
  .day { margin: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: var(--rule); border: 1px solid var(--rule); border-radius: 12px; overflow: hidden; }
  .day div { background: var(--surface); padding: 8px; display: flex; flex-direction: column-reverse; justify-content: flex-end; gap: 2px; }
  .day dt { font-size: 11px; color: var(--fog); line-height: 1.25; }
  .day dd { margin: 0; font-size: 17px; font-weight: 800; line-height: 1.1; }
</style>

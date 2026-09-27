<script lang="ts">
  // One direction of a route as a straight line: stops as ticks, buses at their real distance
  // along it (travel is left to right), buses arriving together in amber, long waits in red.
  import { minutes } from "../lib/format";
  import { isLongWait, isTogether } from "../lib/problems";
  import type { Pattern, Vehicle } from "../lib/types";

  let { pattern, buses, scheduled, selected = null, onSelect }: {
    pattern: Pattern;
    buses: Vehicle[]; // on this pattern
    scheduled: number | null; // seconds between buses right now
    selected?: string | null;
    onSelect: (vid: string) => void;
  } = $props();

  const W = 340, PAD = 14;
  const length = $derived(Math.max(1, ...pattern.s.map((s) => s[3])));
  const x = (feet: number | null) => PAD + Math.max(0, Math.min(1, (feet ?? 0) / length)) * (W - 2 * PAD);
  const ordered = $derived([...buses].filter((b) => b.pdist != null).sort((a, b) => (b.pdist ?? 0) - (a.pdist ?? 0)));
  const byVid = $derived(new Map(buses.map((b) => [b.vid, b])));
  const together = $derived(new Set(ordered.flatMap((b, i) =>
    isTogether(b, scheduled) && ordered[i - 1] ? [b.vid, ordered[i - 1].vid] : [])));
  const waits = $derived(ordered.flatMap((b) => {
    const lead = b.ahead ? byVid.get(b.ahead) : undefined;
    return lead && isLongWait(b, scheduled) ? [{ from: x(b.pdist), to: x(lead.pdist), label: minutes(b.gap_s ?? 0) }] : [];
  }));
  // Room above the line only when there is a wait label to show.
  const Y = $derived(waits.length ? 34 : 16);
  const cut = (t: string) => (t.length > 24 ? `${t.slice(0, 23)}…` : t);
</script>

<svg viewBox="0 0 {W} {Y + 30}" role="img" aria-label="{buses.length} buses along the route, from {pattern.s[0]?.[2]} to {pattern.s.at(-1)?.[2]}">
  <line x1={PAD} y1={Y} x2={W - PAD} y2={Y} class="track" />
  {#each pattern.s as stop}<line x1={x(stop[3])} y1={Y - 3} x2={x(stop[3])} y2={Y + 3} class="tick" />{/each}
  {#each waits as w}
    <line x1={w.from} y1={Y} x2={w.to} y2={Y} class="wait" />
    <text x={(w.from + w.to) / 2} y={Y - 12} class="wait-label">{w.label}</text>
  {/each}
  {#each ordered as b (b.vid)}
    <g class="bus" role="button" tabindex="0" aria-label="Bus {b.vid}"
      onclick={() => onSelect(b.vid)} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(b.vid)}>
      <circle cx={x(b.pdist)} cy={Y} r="15" class="hit" />
      {#if b.vid === selected}<circle cx={x(b.pdist)} cy={Y} r="10.5" class="ring" />{/if}
      <circle cx={x(b.pdist)} cy={Y} r="6.5" class="dot" class:together={together.has(b.vid)} />
    </g>
  {/each}
  <text x={PAD} y={Y + 26} class="end">{cut(pattern.s[0]?.[2] ?? "")}</text>
  <text x={W - PAD} y={Y + 26} class="end" text-anchor="end">{cut(pattern.s.at(-1)?.[2] ?? "")} →</text>
</svg>

<style>
  svg { width: 100%; height: auto; display: block; overflow: visible; }
  .track { stroke: var(--rule); stroke-width: 6; stroke-linecap: round; }
  .tick { stroke: var(--surface); stroke-width: 1; }
  .wait { stroke: var(--star-red); stroke-width: 6; stroke-linecap: round; opacity: 0.4; }
  .wait-label { fill: var(--star-red); font-size: 12px; font-weight: 800; text-anchor: middle; }
  .end { fill: var(--fog); font-size: 11px; }
  .hit { fill: transparent; }
  .bus { cursor: pointer; outline: none; }
  .bus:focus-visible .dot { stroke: var(--asphalt); }
  .dot { fill: var(--flag-blue); stroke: var(--surface); stroke-width: 2; }
  .dot.together { fill: var(--signal-amber); }
  .ring { fill: none; stroke: var(--asphalt); stroke-width: 2.5; }
</style>

<script lang="ts">
  // One direction of a line as a straight line, as a bus route's is drawn: stations as ticks at
  // their real distances (travel is left to right), its trains in the line's colour between their
  // last and next station (lib/trains.ts strip), and the minutes between them by CTA's own times
  // (lib/trains.ts gaps). Transfer stations are named where they fit. Tap a train to open it.
  import { fitEnds, place } from "../lib/labels";
  import { swarm } from "../lib/swarm";
  import type { Strip } from "../lib/trains";
  import type { Lines } from "../lib/types";

  let { s, gaps = [], together = new Set(), lines, color, dest, selected = null, onSelect }: {
    s: Strip;
    gaps?: { from: number; to: number; s: number; long: boolean; together?: boolean }[]; // our claims: red, amber
    together?: Set<string>; // trains running together (amber)
    lines: Lines | null;
    color: string | undefined;
    dest: string; // where these trains are going ("Howard", "Loop")
    selected?: string | null;
    onSelect: (key: string) => void;
  } = $props();

  const W = 340, PAD = 14, GAP = 14, NAME_CHAR = 5.6, MIN_CHAR = 6.4;
  const x = (f: number) => PAD + f * (W - 2 * PAD);
  const station = (id: string) => lines?.stations[id];
  const key = (p: Strip["trains"][number]) => `${p.t.rt}:${p.t.rn}`;
  // Station names where they fit: transfer stations (other lines meet this one) first, then
  // the ones farthest from any named place, so names spread along the line
  const lineCount = (id: string) => station(id)?.lines.length ?? 1;
  const names = $derived.by(() => {
    const anchors = [0, 1, ...s.seq.flatMap((id, i) => (lineCount(id) >= 2 ? [s.at[i]] : []))];
    return place(s.seq.slice(1, -1).map((id, k) => ({
      x: x(s.at[k + 1]),
      text: (station(id)?.name ?? "").replace(/\s*\(.*\)$/, ""), // "Belmont (Red/Brown/Purple)": the line is known here
      weight: lineCount(id) >= 2 ? 10 + lineCount(id) : Math.min(...anchors.map((a) => Math.abs(a - s.at[k + 1]))),
    })), PAD, W - PAD, NAME_CHAR, 8, 5);
  });
  // Trains too close to tell apart leave the line just enough not to touch (lib/swarm)
  const dy = $derived(swarm(s.trains.map((p) => x(p.x)), GAP));
  const spread = $derived(Math.max(0, ...dy.map(Math.abs)));
  const crowded = (i: number) => s.trains.some((o, j) => j !== i && Math.hypot(x(o.x) - x(s.trains[i].x), dy[j] - dy[i]) < 30);
  // each gap's minutes over its middle, the longest first where labels would meet
  const mins = $derived(place(
    gaps.map((g) => ({ x: (x(g.from) + x(g.to)) / 2, text: `${Math.max(1, Math.round(g.s / 60))} min`, weight: g.s + (g.long ? 1e6 : 0) })),
    0, W, MIN_CHAR, 6, 10,
  ));
  const longAt = $derived(new Set(gaps.filter((g) => g.long).map((g) => (x(g.from) + x(g.to)) / 2)));
  const togetherAt = $derived(new Set(gaps.filter((g) => g.together).map((g) => (x(g.from) + x(g.to)) / 2)));
  const Y = $derived((mins.length ? 30 : 16) + spread);
  const namesY = $derived(Y + spread + 22);
  const endsY = $derived(namesY + (names.length ? 16 : 0));
  const first = $derived(station(s.seq[0])?.name ?? "");
  const ends = $derived(fitEnds(first, dest, W - 2 * PAD, 30, (t) => t.length * 6.4));
</script>

<svg viewBox="0 0 {W} {endsY + 6}" role="img" aria-label="{s.trains.length} trains toward {dest}, from {first}">
  <line x1={PAD} y1={Y} x2={W - PAD} y2={Y} class="track" />
  {#each s.at as a}<line x1={x(a)} y1={Y - 3} x2={x(a)} y2={Y + 3} class="tick" />{/each}
  {#each names as n}<line x1={n.x} y1={Y + 5} x2={n.x} y2={namesY - 10} class="name-tick" />{/each}
  {#each gaps.filter((g) => g.long) as g}<line x1={x(g.from)} y1={Y} x2={x(g.to)} y2={Y} class="wait" />{/each}
  {#each mins as m}<text x={m.x} y={Y - 12 - spread} class="min" class:long={longAt.has(m.x)} class:together={togetherAt.has(m.x)}>{m.text}</text>{/each}
  {#each s.trains as p, i (key(p))}
    <g class="train" role="button" tabindex="0" aria-label="Train {p.t.rn}"
      onclick={() => onSelect(key(p))} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(key(p))}>
      <circle cx={x(p.x)} cy={Y + dy[i]} r={crowded(i) ? GAP / 2 : 15} class="hit" />
      {#if key(p) === selected}<circle cx={x(p.x)} cy={Y + dy[i]} r="11" class="ring" />{/if}
      <rect x={x(p.x) - 6.5} y={Y + dy[i] - 6.5} width="13" height="13" rx="3" class="sq" class:together={together.has(key(p))} style:fill={color} />
    </g>
  {/each}
  {#each names as n}<text x={n.x} y={namesY} class="name">{n.text}</text>{/each}
  <text x={PAD} y={endsY} class="end">{ends[0]}</text>
  <text x={W - PAD} y={endsY} class="end" text-anchor="end">{ends[1]} →</text>
</svg>

<style>
  svg { width: 100%; height: auto; display: block; overflow: visible; }
  .track { stroke: var(--rule); stroke-width: 6; stroke-linecap: round; }
  .tick { stroke: var(--surface); stroke-width: 1; }
  .name-tick { stroke: var(--rule); stroke-width: 1; }
  .name { fill: var(--fog); font-size: 10px; text-anchor: middle; }
  .min { fill: var(--asphalt); font-size: 11px; font-weight: 700; text-anchor: middle; font-variant-numeric: tabular-nums; }
  .min.long { fill: var(--star-red); font-size: 12px; font-weight: 800; }
  .min.together { fill: var(--signal-amber); }
  .wait { stroke: var(--star-red); stroke-width: 6; stroke-linecap: round; opacity: 0.4; pointer-events: none; }
  .end { fill: var(--asphalt); font-size: 11px; font-weight: 600; }
  .hit { fill: transparent; }
  .train { cursor: pointer; outline: none; }
  .train:focus-visible .sq { stroke: var(--asphalt); }
  .sq { stroke: var(--surface); stroke-width: 2; }
  .sq.together { stroke: var(--signal-amber); stroke-width: 3; } /* running together: an amber outline */
  .ring { fill: none; stroke: var(--asphalt); stroke-width: 2.5; }
</style>

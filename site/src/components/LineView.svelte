<script lang="ts">
  // One direction of a route as a straight line: stops as ticks, buses at their real distance
  // along it (travel is left to right), buses arriving together in amber, long waits in red.
  // Rows, top to bottom, so text in one row can't meet text in another: wait times, the line,
  // major cross streets, the two ends, and the stop you tapped. Within a row, labels are
  // placed only where they fit (lib/labels.ts).
  import { minutes } from "../lib/format";
  import { crossStreet, ownStreet, place } from "../lib/labels";
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
  const STREET_CHAR = 5.6, WAIT_CHAR = 7.2; // upper bounds on character width at 10 / 12 px
  const length = $derived(Math.max(1, ...pattern.s.map((s) => s[3])));
  const x = (feet: number | null) => PAD + Math.max(0, Math.min(1, (feet ?? 0) / length)) * (W - 2 * PAD);
  const ordered = $derived([...buses].filter((b) => b.pdist != null).sort((a, b) => (b.pdist ?? 0) - (a.pdist ?? 0)));
  const byVid = $derived(new Map(buses.map((b) => [b.vid, b])));
  const together = $derived(new Set(ordered.flatMap((b, i) =>
    isTogether(b, scheduled) && ordered[i - 1] ? [b.vid, ordered[i - 1].vid] : [])));
  const waits = $derived(ordered.flatMap((b) => {
    const lead = b.ahead ? byVid.get(b.ahead) : undefined;
    return lead && isLongWait(b, scheduled)
      ? [{ from: x(b.pdist), to: x(lead.pdist), label: minutes(b.gap_s ?? 0), s: b.gap_s ?? 0 }]
      : [];
  }));
  // Every red bar shows; its time label only where it fits (the longest waits first).
  const waitLabels = $derived(place(
    waits.map((w) => ({ x: (w.from + w.to) / 2, text: w.label, weight: w.s })), 0, W, WAIT_CHAR, 6, 10,
  ));

  // Express stretches: long runs with no stops (J14 on Lake Shore Drive: 7.3 mi). Drawn thin
  // and dashed, labelled, and a tap there says so instead of jumping to a stop miles away.
  const EXPRESS_PX = 40;
  const express = $derived(pattern.s.slice(1).flatMap((b, i) => {
    const a = pattern.s[i];
    return x(b[3]) - x(a[3]) >= EXPRESS_PX ? [{ from: x(a[3]), to: x(b[3]), miles: (b[3] - a[3]) / 5280 }] : [];
  }));
  const isExpress = (label: string) => label.startsWith("no stops");
  const miles = (m: number) => (m >= 2 ? `${Math.round(m)} mi` : `${m.toFixed(1)} mi`);

  // Major cross streets: the stops most routes cross (transfer points), shortened to the cross
  // street ("Belmont" on Clark). Not the two ends: they have their own row. Express labels
  // share the row and win any clash.
  const own = $derived(ownStreet(pattern.s.map((s) => s[2])));
  const streets = $derived(place(
    [
      ...express.map((e) => ({ x: (e.from + e.to) / 2, text: `no stops for ${miles(e.miles)}`, weight: 1000 })),
      ...pattern.s.slice(1, -1)
        .filter((s) => (s[4] ?? 1) >= 2)
        .map((s) => ({ x: x(s[3]), text: crossStreet(s[2], own), weight: s[4] ?? 1 })),
    ],
    PAD, W - PAD, STREET_CHAR,
  ));

  // Buses too close to tell apart on the line (a couple of blocks, at this scale) stack
  // vertically at that spot, so two buses always read as two dots.
  const GAP = 14; // px between dot centres
  const stacked = $derived.by(() => {
    const dy = new Map<string, number>();
    let group: Vehicle[] = [];
    const flush = () => group.forEach((b, i) => dy.set(b.vid, group.length > 1 ? (i - (group.length - 1) / 2) * GAP : NaN));
    for (const b of ordered) {
      if (group.length && x(group.at(-1)!.pdist) - x(b.pdist) >= GAP) { flush(); group = []; }
      group.push(b);
    }
    flush();
    return dy;
  });
  const spread = $derived(Math.max(0, ...[...stacked.values()].filter((d) => !isNaN(d)))); // tallest stack's half-height
  const inStack = (b: Vehicle) => !isNaN(stacked.get(b.vid) ?? NaN);
  const cy = (b: Vehicle) => Y + (inStack(b) ? stacked.get(b.vid)! : 0);

  // Tap the line: the nearest stop is marked and named, to find your stop against the gaps.
  let picked = $state<{ x: number; name: string } | null>(null);
  let svg: SVGSVGElement;
  function pick(e: MouseEvent) {
    const m = svg.getScreenCTM();
    if (!m) return;
    const px = Math.max(PAD, Math.min(W - PAD, new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse()).x));
    const feet = ((px - PAD) / (W - 2 * PAD)) * length;
    const run = express.find((e) => px > e.from + 6 && px < e.to - 6);
    const stop = pattern.s.reduce((a, b) => (Math.abs(b[3] - feet) < Math.abs(a[3] - feet) ? b : a));
    const next = run
      ? { x: px, name: `Express: no stops for ${run.miles.toFixed(1)} mi` }
      : { x: x(stop[3]), name: stop[2] };
    picked = picked?.name === next.name ? null : next;
  }
  $effect(() => { void pattern; picked = null; }); // a different route: forget the stop

  // Rows (y of each baseline), from the top.
  const Y = $derived((waits.length ? 34 : 16) + spread); // the line
  const streetsY = $derived(Y + spread + 22);
  const endsY = $derived(streetsY + (streets.length ? 16 : 0));
  const pickedY = $derived(endsY + 17);
  const height = $derived((picked ? pickedY : endsY) + 6);
  const cut = (t: string) => (t.length > 24 ? `${t.slice(0, 23)}…` : t);
  const clampX = (cx: number, text: string) => Math.max(PAD + (text.length * 6.4) / 2, Math.min(W - PAD - (text.length * 6.4) / 2, cx));
</script>

<svg bind:this={svg} viewBox="0 0 {W} {height}" role="img"
  aria-label="{buses.length} buses along the route, from {pattern.s[0]?.[2]} to {pattern.s.at(-1)?.[2]}">
  <line x1={PAD} y1={Y} x2={W - PAD} y2={Y} class="track" />
  {#each express as e}<line x1={e.from + 4} y1={Y} x2={e.to - 4} y2={Y} class="express" />{/each}
  {#each pattern.s as stop}<line x1={x(stop[3])} y1={Y - 3} x2={x(stop[3])} y2={Y + 3} class="tick" />{/each}
  <!-- the tap target for "which stop is this": under the buses, so a bus tap wins -->
  <rect x={PAD - 6} y={Y - 12} width={W - 2 * PAD + 12} height="24" class="pickable" onclick={pick} role="presentation" />
  {#each waits as w}<line x1={w.from} y1={Y} x2={w.to} y2={Y} class="wait" />{/each}
  {#each waitLabels as l}<text x={l.x} y={Y - 12 - spread} class="wait-label">{l.text}</text>{/each}
  {#each streets as s}{#if !isExpress(s.text)}<line x1={s.x} y1={Y + 5} x2={s.x} y2={streetsY - 10} class="street-tick" />{/if}{/each}
  {#if picked}<circle cx={picked.x} cy={Y} r="4.5" class="picked" />{/if}
  {#each ordered as b (b.vid)}
    <g class="bus" role="button" tabindex="0" aria-label="Bus {b.vid}"
      onclick={() => onSelect(b.vid)} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(b.vid)}>
      <circle cx={x(b.pdist)} cy={cy(b)} r={inStack(b) ? GAP / 2 : 15} class="hit" />
      {#if b.vid === selected}<circle cx={x(b.pdist)} cy={cy(b)} r="10.5" class="ring" />{/if}
      <circle cx={x(b.pdist)} cy={cy(b)} r="6.5" class="dot" class:together={together.has(b.vid)} />
    </g>
  {/each}
  {#each streets as s}<text x={s.x} y={streetsY} class="street" class:express-label={isExpress(s.text)}>{s.text}</text>{/each}
  <text x={PAD} y={endsY} class="end">{cut(pattern.s[0]?.[2] ?? "")}</text>
  <text x={W - PAD} y={endsY} class="end" text-anchor="end">{cut(pattern.s.at(-1)?.[2] ?? "")} →</text>
  {#if picked}
    <text x={clampX(picked.x, picked.name)} y={pickedY} class="picked-name">{picked.name}</text>
  {/if}
</svg>

<style>
  svg { width: 100%; height: auto; display: block; overflow: visible; }
  .track { stroke: var(--rule); stroke-width: 6; stroke-linecap: round; }
  .tick { stroke: var(--surface); stroke-width: 1; }
  .express { stroke: var(--surface); stroke-width: 3; stroke-dasharray: 5 4; } /* thins the track: no stops */
  .pickable { fill: transparent; cursor: pointer; }
  .wait { stroke: var(--star-red); stroke-width: 6; stroke-linecap: round; opacity: 0.4; pointer-events: none; }
  .wait-label { fill: var(--star-red); font-size: 12px; font-weight: 800; text-anchor: middle; }
  .street { fill: var(--fog); font-size: 10px; text-anchor: middle; }
  .street-tick { stroke: var(--rule); stroke-width: 1; }
  .express-label { font-style: italic; }
  .end { fill: var(--asphalt); font-size: 11px; font-weight: 600; } /* the route's two ends outrank its cross streets */
  .picked { fill: var(--surface); stroke: var(--asphalt); stroke-width: 2.5; pointer-events: none; }
  .picked-name { fill: var(--asphalt); font-size: 11px; font-weight: 700; text-anchor: middle; }
  .hit { fill: transparent; }
  .bus { cursor: pointer; outline: none; }
  .bus:focus-visible .dot { stroke: var(--asphalt); }
  .dot { fill: var(--flag-blue); stroke: var(--surface); stroke-width: 2; }
  .dot.together { fill: var(--signal-amber); }
  .ring { fill: none; stroke: var(--asphalt); stroke-width: 2.5; }
</style>

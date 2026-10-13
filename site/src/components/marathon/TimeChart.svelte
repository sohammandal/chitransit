<script lang="ts">
  // One measure through a day: Marathon Sunday's line against what normal Sundays did (a band
  // from the lower to the higher of the two, so the gap between them reads as the noise). Each
  // value is a bin of `step` minutes, drawn at its middle. Drawn at the container's own width so
  // its text stays readable on a phone; moments are numbered on the chart and labeled under it
  // (at 390 px two labels a couple of hours apart can't sit side by side). Hover, a tap or a
  // drag, or the arrow keys show every value at one bin, in a card that stays inside the chart.
  import { clock, type Moment } from "../../lib/marathon";

  type Line = { values: (number | null)[]; label: string; tone: "red" | "blue" | "amber" | "fog"; dashed?: boolean };
  type Band = { lo: (number | null)[]; hi: (number | null)[]; label: string };
  type Span = { from: number; to: number; label: string }; // minutes after midnight
  type Mark = { at: number; label: string };

  let {
    start, step, lines, band, spans = [], marks = [], moments = [], max, unit = "", label, title, yLabel, height = 230, ticks,
  }: {
    start: number; // minutes after midnight of the first value
    step: number; // minutes between values: each one counts the bin from its time to the next
    lines: Line[];
    band?: Band;
    spans?: Span[];
    marks?: Mark[]; // labeled inside the chart: for one or two far apart
    moments?: Moment[]; // numbered on the chart, labeled under it
    max?: number;
    unit?: string; // after the numbers: "%", " min"
    label: string; // what the chart says, for screen readers
    title?: string; // one line above the chart
    yLabel?: string; // what the numbers are: "% of a normal Sunday"
    height?: number;
    ticks?: number[];
  } = $props();

  const LEFT = 42, RIGHT = 12, BOTTOM = 26, PIN = 10, GAP = 10; // PIN: a moment's badge radius

  let width = $state(0);
  let plot: HTMLDivElement | undefined = $state();
  let at = $state<number | null>(null); // the bin under the crosshair
  let said = $state(""); // the live region's text
  let cardW = $state(0);
  let cardH = $state(0);

  const isNum = (v: number | null | undefined): v is number => v != null;
  const n = $derived(Math.max(...lines.map((l) => l.values.length)));
  const end = $derived(start + step * n);
  const x = (min: number) => LEFT + ((min - start) / (end - start || 1)) * (width - LEFT - RIGHT);
  const xi = (i: number) => x(start + (i + 0.5) * step);
  // moments in time order, numbered; a badge that would touch an earlier one drops a row
  const pins = $derived.by(() => {
    const out: (Moment & { n: number; px: number; row: number })[] = [];
    [...moments].sort((a, b) => a.at - b.at).forEach((m, k) => {
      const px = x(m.at);
      let row = 0;
      while (out.some((p) => p.row === row && Math.abs(p.px - px) < 2 * PIN + 3)) row++;
      out.push({ ...m, n: k + 1, px, row });
    });
    return out;
  });
  const top = $derived(Math.max(spans.length || marks.length ? 26 : 14, ...pins.map((p) => 2 * PIN + 10 + p.row * (2 * PIN + 3))));
  const ymax = $derived(max ?? niceCeil(Math.max(...lines.flatMap((l) => l.values.filter(isNum)), ...(band?.hi ?? []).filter(isNum))));
  const yTicks = $derived(ticks ?? [0, ymax / 2, ymax]);
  const y = (v: number) => top + (1 - v / (ymax || 1)) * (height - top - BOTTOM);
  const hours = $derived([6, 9, 12, 15, 18, 21].map((h) => h * 60).filter((m) => m >= start && m <= end));

  function niceCeil(v: number): number {
    if (!isFinite(v) || v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    return [1, 2, 2.5, 5, 10].map((k) => k * p).find((c) => c >= v) ?? 10 * p;
  }

  // A line through the values, broken where one is missing
  function path(values: (number | null)[]): string {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${xi(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  }

  function area(lo: (number | null)[], hi: (number | null)[]): string {
    const pts = hi.map((v, i) => [i, v, lo[i]] as const).filter(([, h, l]) => h != null && l != null);
    if (!pts.length) return "";
    const upper = pts.map(([i, h]) => `${xi(i).toFixed(1)},${y(h!).toFixed(1)}`);
    const lower = pts.reverse().map(([i, , l]) => `${xi(i).toFixed(1)},${y(l!).toFixed(1)}`);
    return `M${upper.join("L")}L${lower.join("L")}Z`;
  }

  // ---- the readout at the crosshair's bin ----
  // what the key and the card list: each line, then the band
  const keys = $derived([...lines.map((l) => ({ cls: l.tone + (l.dashed ? " dashed" : ""), label: l.label })), ...(band ? [{ cls: "band", label: band.label }] : [])]);
  const num = (v: number) => Math.round(v).toLocaleString("en-US");
  // its time ("1:45–2 PM", "11:45 AM–noon": AM or PM said once), a moment in it, and each value
  // in the keys' order (the band's as its range; null where it has none)
  const read = $derived.by(() => {
    if (at == null) return null;
    const i = at;
    const [a, b] = [clock(start + i * step), clock(start + (i + 1) * step)];
    const m = / [AP]M$/.exec(a)?.[0];
    const [lo, hi] = [band?.lo[i], band?.hi[i]];
    const range = !isNum(lo) || !isNum(hi) ? null : num(lo) === num(hi) ? num(lo) + unit : `${num(lo)}–${num(hi)}${unit}`;
    return {
      when: `${m && b.endsWith(m) ? a.slice(0, -3) : a}–${b}`,
      pin: pins.find((p) => Math.floor((p.at - start) / step) === i),
      values: [...lines.map((l) => { const v = l.values[i]; return isNum(v) ? num(v) + unit : "no data"; }), ...(band ? [range] : [])],
    };
  });
  const dots = $derived(at == null ? [] : lines.flatMap((l) => { const v = l.values[at!]; return isNum(v) ? [{ tone: l.tone, cy: y(v) }] : []; }));
  // for a screen reader: "4 to 4:15 AM: Buses out 97%, ..."
  function speak() {
    if (read) said = `${read.when}: ${keys.flatMap((k, j) => (read.values[j] ? [`${k.label} ${read.values[j]}`] : [])).join(", ")}`.replaceAll("–", " to ");
  }

  // The tallest stretch of the plot clear of these heights (6 px around each)
  function clear(ys: number[]): [number, number] {
    const cuts = [top - 4, ...ys.sort((a, b) => a - b), height - BOTTOM + 4];
    let best: [number, number] = [top, top];
    for (let k = 1; k < cuts.length; k++) {
      const [a, b] = [cuts[k - 1] + 6, cuts[k] - 6];
      if (b - a > best[1] - best[0]) best = [a, b];
    }
    return best;
  }

  // The card: beside the crosshair where it fits (right first), else over it (a phone's middle),
  // never past the chart's edges; up and down, where it hides none of the values at the crosshair
  // (nor the band's, when there's room), held to the plot's edge there so it stays put
  const card = $derived.by(() => {
    if (at == null || !width) return null;
    const cx = xi(at);
    const w = Math.min(cardW, width);
    const left = width - cx - GAP >= w ? cx + GAP : cx - GAP >= w ? cx - GAP - w : Math.min(Math.max(cx - w / 2, 0), width - w);
    const ys = dots.map((d) => d.cy);
    let [a, b] = clear([...ys, ...[band?.lo[at], band?.hi[at]].filter(isNum).map(y)]);
    if (b - a < cardH) [a, b] = clear(ys);
    const y0 = a <= top + 2 ? a : b >= height - BOTTOM - 2 ? b - cardH : (a + b - cardH) / 2;
    return { left, top: Math.max(2, Math.min(y0, height - cardH - 2)) };
  });

  // ---- pointer, touch and keys ----
  function seek(e: PointerEvent) {
    if (!plot) return;
    const r = plot.getBoundingClientRect();
    const i = Math.floor(((e.clientX - r.left - LEFT) / (width - LEFT - RIGHT)) * n);
    at = Math.min(n - 1, Math.max(0, i));
    if (e.pointerType !== "mouse") speak();
  }
  function hide() {
    at = null;
    said = "";
  }
  function key(e: KeyboardEvent) {
    const hour = Math.max(1, Math.round(60 / step));
    const moves: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1 };
    let i: number;
    if (e.key in moves) i = (at ?? (moves[e.key] > 0 ? -1 : n)) + moves[e.key] * (e.shiftKey ? hour : 1);
    else if (e.key === "Home") i = 0;
    else if (e.key === "End") i = n - 1;
    else if (e.key === "Escape" && at != null) return hide();
    else return;
    e.preventDefault();
    at = Math.min(n - 1, Math.max(0, i));
    speak();
  }
</script>

<!-- a tap elsewhere puts the card away (a touch never "leaves" the chart) -->
<svelte:window onpointerdown={(e) => { if (at != null && plot && !plot.contains(e.target as Node)) hide(); }} />

<div class="chart" bind:clientWidth={width}>
  {#if title}<p class="title">{title}</p>{/if}
  <p class="sub">
    <span>{yLabel ?? ""}</span>
    <span class="hint" aria-hidden="true"><span class="touch">Tap the chart for the numbers</span><span class="mouse">Hover over the chart for the numbers</span></span>
  </p>
  {#if width}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <div class="plot" bind:this={plot} role="img" aria-label={label} tabindex="0"
      onpointerdown={seek} onpointermove={seek} onpointercancel={hide}
      onpointerleave={(e) => { if (e.pointerType === "mouse") hide(); }}
      onkeydown={key} onblur={hide}
      onfocus={() => { if (at == null && plot?.matches(":focus-visible")) at = 0; }}>
      <svg {width} {height} aria-hidden="true">
        {#each spans as s}
          <rect class="span" x={x(s.from)} y={top} width={Math.max(0, x(s.to) - x(s.from))} height={height - top - BOTTOM} />
          <text class="span-label" x={x(s.from) + 4} y={top - 8}>{s.label}</text>
        {/each}
        {#each yTicks as t}
          <line class="grid" x1={LEFT} x2={width - RIGHT} y1={y(t)} y2={y(t)} />
          <text class="tick" x={LEFT - 6} y={y(t) + 4} text-anchor="end">{Math.round(t).toLocaleString()}{unit}</text>
        {/each}
        {#each hours as h}
          <text class="tick" x={x(h)} y={height - 6} text-anchor="middle">{clock(h)}</text>
        {/each}
        {#if band}
          <path class="band" d={area(band.lo, band.hi)} />
        {/if}
        {#each marks as m}
          <line class="mark" x1={x(m.at)} x2={x(m.at)} y1={top} y2={height - BOTTOM} />
          <text class="mark-label" x={x(m.at) + 4} y={top + 12}>{m.label}</text>
        {/each}
        {#each pins as p}
          {@const cy = 4 + PIN + p.row * (2 * PIN + 3)}
          <line class="mark moment" x1={p.px} x2={p.px} y1={cy + PIN} y2={height - BOTTOM} />
          <circle class="pin" cx={p.px} {cy} r={PIN} />
          <text class="pin-n" x={p.px} y={cy + 4.5} text-anchor="middle">{p.n}</text>
        {/each}
        {#each lines as l}
          <path class="line {l.tone}" class:dashed={l.dashed} d={path(l.values)} />
        {/each}
        {#if at != null}
          <line class="cross" x1={xi(at)} x2={xi(at)} y1={top} y2={height - BOTTOM} />
          {#each dots as d}<circle class="dot {d.tone}" cx={xi(at)} cy={d.cy} r="4" />{/each}
        {/if}
      </svg>
      {#if read && card}
        <div class="card" aria-hidden="true" bind:offsetWidth={cardW} bind:offsetHeight={cardH}
          style="left: {card.left}px; top: {card.top}px; max-width: {width}px">
          <strong class="when">{read.when}</strong>
          {#if read.pin}<span class="moment-now"><b class="pin">{read.pin.n}</b>{read.pin.label}</span>{/if}
          <span class="rows">
            {#each keys as k, j}
              {#if read.values[j]}<i class="swatch {k.cls}"></i><b>{read.values[j]}</b><span>{k.label}</span>{/if}
            {/each}
          </span>
        </div>
      {/if}
    </div>
    <ul class="key">
      {#each keys as k}<li><i class="swatch {k.cls}"></i>{k.label}</li>{/each}
    </ul>
    {#if pins.length}
      <ol class="moments">
        {#each pins as p}<li><b class="pin">{p.n}</b>{p.label}</li>{/each}
      </ol>
    {/if}
    <p class="sr-only" aria-live="polite">{said}</p>
  {/if}
</div>

<style>
  .chart { width: 100%; min-height: 1px; }
  .title { margin: 0 0 2px; font-size: 16px; line-height: 1.3; font-weight: 700; color: var(--asphalt); }
  .sub { display: flex; justify-content: space-between; gap: 4px 12px; flex-wrap: wrap; margin: 0 0 4px; font-size: 13px; line-height: 1.35; color: var(--fog); }
  .hint .mouse { display: none; }
  @media (hover: hover) and (pointer: fine) {
    .hint .touch { display: none; }
    .hint .mouse { display: inline; }
  }
  /* vertical swipes still scroll the page; sideways drags move the crosshair */
  .plot { position: relative; touch-action: pan-y; border-radius: 6px; -webkit-tap-highlight-color: transparent; }
  svg { display: block; overflow: visible; font-family: var(--font); user-select: none; -webkit-user-select: none; }
  .grid { stroke: var(--rule); stroke-width: 1; }
  .tick { fill: var(--fog); font-size: 13px; }
  .band { fill: var(--fog); opacity: 0.22; }
  .span { fill: var(--star-red); opacity: 0.07; }
  .span-label, .mark-label { fill: var(--fog); font-size: 13px; font-weight: 600; }
  .mark { stroke: var(--fog); stroke-width: 1; stroke-dasharray: 3 3; }
  .pin { fill: var(--fog); }
  .pin-n { fill: var(--surface); font-size: 13px; font-weight: 800; }
  .red { --c: var(--star-red); }
  .blue { --c: var(--flag-blue); }
  .amber { --c: var(--signal-amber); }
  .fog { --c: var(--fog); }
  .line { fill: none; stroke: var(--c); stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
  .line.dashed { stroke-dasharray: 5 4; stroke-width: 2; }
  .cross { stroke: var(--asphalt); stroke-width: 1; opacity: 0.6; }
  .dot { fill: var(--c); stroke: var(--concrete); stroke-width: 2; }
  .card {
    position: absolute; z-index: 1; width: max-content; padding: 7px 10px 8px; pointer-events: none;
    background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; box-shadow: var(--shadow);
    font-size: 13px; line-height: 1.3; color: var(--asphalt);
  }
  .when { display: block; margin-bottom: 3px; font-size: 13px; }
  .moment-now { display: flex; align-items: flex-start; gap: 6px; margin-bottom: 4px; color: var(--fog); }
  .rows { display: grid; grid-template-columns: auto auto minmax(0, 1fr); gap: 2px 6px; align-items: baseline; }
  .rows b { font-size: 14px; white-space: nowrap; }
  .rows span { color: var(--fog); }
  .rows .swatch { align-self: center; }
  .key, .moments { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 6px 0 0; padding: 0; list-style: none; font-size: 13px; line-height: 1.35; color: var(--fog); }
  .key li, .moments li { display: flex; align-items: center; gap: 6px; }
  .moments { gap: 4px 14px; }
  b.pin {
    display: inline-flex; flex: none; align-items: center; justify-content: center; width: 19px; height: 19px; border-radius: 50%;
    background: var(--fog); color: var(--surface); font-size: 13px; font-weight: 800; line-height: 1;
    padding-top: 4px; /* Overpass sets its digits ~2 px high in the line: centred by pixels at 4x */
  }
  .swatch { display: inline-block; width: 18px; height: 3px; border-radius: 2px; background: var(--c); translate: 0 -1.6px; } /* Overpass sets text ~1.7 px high in its line: icons on the text's cap-height centre, measured in Chrome (Oct 12) */
  .swatch.dashed { background: none; border-top: 2px dashed var(--c); height: 0; }
  .swatch.band { height: 10px; background: var(--fog); opacity: 0.3; }
  .card .swatch { width: 14px; }
  .sr-only { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>

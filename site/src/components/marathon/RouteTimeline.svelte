<script lang="ts">
  // Two clocks and the buses: per rerouted route, when CTA's detour flag was on (thin line), and
  // when the stops it cut off were without a bus (thick bar, to when half of them had one back;
  // the whisker runs to the last). Behind them, the window CTA's alerts gave riders. Each row is a
  // button that picks its route for the whole page (pickedRoute): the others dim, and a line under
  // the chart (held at the screen's foot while the chart is in view) says what happened to it.
  import { clock, pickedRoute } from "../../lib/marathon";
  import type { Timeline } from "../../lib/marathon";

  type Route = Timeline["routes"][number];
  let { data }: { data: Timeline } = $props();

  let width = $state(0);
  let tab = $state(0); // the row that takes Tab; the arrow keys move between rows
  const [ROW, TOP, BOTTOM, RIGHT] = [20, 34, 24, 10];
  const [from, to] = [5 * 60, 22 * 60];
  const wide = $derived(width >= 560); // room for the routes' names beside their numbers
  const left = $derived(wide ? 150 : 40);
  const height = $derived(TOP + data.routes.length * ROW + BOTTOM);
  const x = (min: number) => left + ((Math.min(Math.max(min, from), to) - from) / (to - from)) * (width - left - RIGHT);
  const hours = [6, 9, 12, 15, 18, 21].map((h) => h * 60);
  const label = $derived(
    `${data.routes.length} rerouted routes. CTA's race-day alerts said ${clock(data.alerts[0])} to ${clock(data.alerts[1])}; ` +
      `${data.routes.filter((r) => r.cut && (!r.back || r.back[1] == null || r.back[1] > data.alerts[1])).length} routes had half their cut-off stops still without a bus after that`,
  );
  const picked = $derived(data.routes.find((r) => r.rt === $pickedRoute) ?? null);
  const tag = (r: Route) => `${/^\d/.test(r.rt) ? "#" : ""}${r.rt}`; // "#22", "J14"
  const name = (r: Route) => `${tag(r)} ${r.name.trim()}`;

  // A row's marks as path data, y = 0 at its middle: the stops' outage (from when they lost their
  // buses, an arrowhead if before 5 AM, to when half had one back; an arrowhead: none that day),
  // the whisker to the last, and the detour flag above (an arrowhead: still on the next morning)
  const tip = (at: number, dir: number, y: number) => `M${x(at) - 4 * dir},${y - 4}L${x(at) + 2 * dir},${y}L${x(at) - 4 * dir},${y + 4}`;
  const rows = $derived(
    data.routes.map((r) => ({
      out: r.cut ? `M${x(r.out)},1H${x(r.back?.[1] ?? to)}` : "",
      tips: r.cut ? (r.out < from ? tip(from, -1, 1) : "") + (r.back ? "" : tip(to, 1, 1)) : "",
      whisker: r.cut && r.back && r.back[1] != null ? `M${x(r.back[1])},1H${x(r.back[2])}M${x(r.back[2])},-2V4` : "",
      flag: `M${x(r.flag[0])},-6H${x(r.flag[1] ?? to)}${r.flag[1] == null ? `M${x(to) - 5},-9L${x(to)},-6L${x(to) - 5},-3` : ""}`,
    })),
  );
  const summary = $derived(
    !picked ? "" :
      `detour flag ${picked.flag[1] == null ? `on from ${clock(picked.flag[0])}, still on the next morning` : `${clock(picked.flag[0])} to ${clock(picked.flag[1])}`}; ` +
      (!picked.back ? `its ${picked.cut} cut-off stops had no ${tag(picked)} bus again that day`
        : picked.back[1] == null ? `fewer than half its ${picked.cut} cut-off stops had a ${tag(picked)} bus again that day`
        : `half its ${picked.cut} cut-off stops had a ${tag(picked)} bus again by ${clock(picked.back[1])}${picked.never ? "" : `, all by ${clock(picked.back[2])}`}`),
  );

  $effect(() => {
    const i = data.routes.indexOf(picked!);
    if (i >= 0) tab = i;
  });

  function key(e: KeyboardEvent, i: number) {
    const j = ({ ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: data.routes.length - 1 } as Record<string, number>)[e.key];
    if (!(j >= 0 && j < data.routes.length)) return;
    e.preventDefault();
    tab = j;
    ((e.currentTarget as HTMLElement).parentElement!.children[j] as HTMLElement).focus();
  }

  function toMap() {
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById("route-explorer")?.scrollIntoView({ behavior: still ? "auto" : "smooth" });
  }
</script>

<div class="timeline" class:wide class:picking={!!picked} bind:clientWidth={width}>
  {#if width}
    <ul class="key">
      <li><i class="k-alert"></i>CTA's race-day alerts</li>
      <li><i class="k-flag"></i>Detour flag in Bus Tracker</li>
      <li><i class="k-out"></i>Its cut-off stops, until half had a bus again</li>
      <li><i class="k-whisker"></i>Until the last did</li>
    </ul>
    <div class="plot">
      <svg {width} {height} role="img" aria-label={label}>
        <rect class="alert" x={x(data.alerts[0])} y={TOP - 6} width={x(data.alerts[1]) - x(data.alerts[0])} height={height - TOP - BOTTOM + 6} />
        <text class="alert-label" x={x(data.alerts[0]) + 4} y={TOP - 12}>CTA's race-day alerts: {clock(data.alerts[0])} to {clock(data.alerts[1])}</text>
        {#each hours as h}
          <path class="grid" d="M{x(h)},{TOP - 6}V{height - BOTTOM}" />
          <text class="tick" x={x(h)} y={height - 6}>{clock(h)}</text>
        {/each}
        {#each rows as m, i}
          <g transform="translate(0,{TOP + i * ROW + ROW / 2})" class:dim={picked && picked !== data.routes[i]}>
            <rect class="band" class:on={picked === data.routes[i]} y={-ROW / 2} {width} height={ROW} rx="4" />
            <path class="out" d={m.out} /><path class="tips" d={m.tips} /><path class="whisker" d={m.whisker} /><path class="flag" d={m.flag} />
          </g>
        {/each}
      </svg>
      <!-- one button per row, over the drawing: tap, click or Enter picks the route; again: all routes -->
      <div class="rows" role="group" aria-label="Rerouted routes: pick one">
        {#each data.routes as r, i (r.rt)}
          <button type="button" class="row" style="top:{TOP + i * ROW}px" tabindex={i === tab ? 0 : -1} aria-pressed={picked === r} aria-label={name(r)}
            onclick={() => pickedRoute.update((p) => (p === r.rt ? "" : r.rt))} onkeydown={(e) => key(e, i)}>
            <span class="lab" style="width:{left - 8}px"><b>{r.rt}</b><span class="nm">{r.name.trim()}</span></span>
          </button>
        {/each}
      </div>
    </div>
    <div class="summary" class:stick={!!picked} aria-live="polite">
      {#if picked}
        <p><b>{name(picked)}</b>: {summary}</p>
        <!-- the route explorer, when the page has one: the route's card and map -->
        {#if document.getElementById("route-explorer")}<button type="button" onclick={toMap}>Map</button>{/if}
        <button type="button" class="all" aria-label="Show all routes" onclick={() => pickedRoute.set("")}>&times;</button>
      {:else}
        <p class="hint">Tap a route to pick it</p>
      {/if}
    </div>
  {/if}
</div>

<style>
  .timeline { width: 100%; min-height: 1px; }
  .plot { position: relative; }
  svg { display: block; font-family: var(--font); }
  path { fill: none; }
  .alert { fill: var(--flag-blue); opacity: 0.1; }
  .alert-label { fill: var(--flag-blue); font-size: 13px; font-weight: 700; }
  .grid { stroke: var(--rule); stroke-width: 1; }
  .tick { fill: var(--fog); font-size: 13px; text-anchor: middle; }
  .band { fill: var(--asphalt); opacity: 0; }
  .band.on { opacity: 0.08; }
  g { transition: opacity 0.15s; }
  .dim { opacity: 0.22; }
  .flag { stroke: var(--asphalt); stroke-width: 1.5; }
  .out { stroke: var(--star-red); stroke-width: 7; }
  .tips { fill: var(--star-red); }
  .whisker { stroke: var(--star-red); stroke-width: 1.5; }

  .row {
    position: absolute; left: 0; right: 0; height: 20px; display: flex; align-items: center; padding: 0; margin: 0;
    background: none; border: 0; border-radius: 4px; color: var(--asphalt); font: inherit; cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .row:focus-visible { outline-offset: -2px; }
  @media (hover: hover) { .row:hover { background: color-mix(in srgb, var(--asphalt) 6%, transparent); } }
  .lab { display: flex; justify-content: flex-end; align-items: center; gap: 8px; padding-right: 2px; font-size: 13px; line-height: 1; white-space: nowrap; }
  .lab b { font-weight: 700; }
  .wide .lab { justify-content: flex-start; }
  .wide .lab b { flex: none; width: 30px; text-align: right; }
  .nm { display: none; }
  .wide .nm { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--fog); }
  .picking .row:not([aria-pressed="true"]) .lab { opacity: 0.4; }
  .row[aria-pressed="true"] .lab b, .row[aria-pressed="true"] .nm { color: var(--flag-blue); }
  /* narrow: the name shows over the row above while hovered or focused */
  .timeline:not(.wide) .nm {
    position: absolute; left: 40px; bottom: calc(100% - 2px); z-index: 1; max-width: calc(100% - 50px);
    padding: 2px 7px; border: 1px solid var(--rule); border-radius: 6px; background: var(--surface); color: var(--asphalt);
    overflow: hidden; text-overflow: ellipsis;
  }
  .timeline:not(.wide) .row:focus-visible .nm { display: block; }
  @media (hover: hover) { .timeline:not(.wide) .row:hover .nm { display: block; } }

  .summary { display: flex; align-items: center; gap: 4px; min-height: 52px; margin-top: 6px; font-size: 14px; line-height: 1.4; }
  .summary p { flex: 1; margin: 0; }
  .summary.stick {
    position: sticky; bottom: calc(8px + var(--safe-bottom)); z-index: 2; padding: 6px 4px 6px 12px;
    background: var(--surface); border: 1px solid var(--rule); border-radius: 12px; box-shadow: var(--shadow);
  }
  .summary button {
    flex: none; min-width: 44px; min-height: 44px; padding: 0 10px; border: 0; border-radius: 10px; cursor: pointer;
    background: var(--surface2); color: var(--asphalt); font: 700 14px var(--font);
  }
  .summary .all { font-size: 22px; font-weight: 400; background: none; }
  .hint { color: var(--fog); }

  .key { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0 0 6px; padding: 0; list-style: none; font-size: 13px; color: var(--fog); }
  .key li { display: flex; align-items: center; gap: 6px; }
  .key i { display: inline-block; width: 18px; translate: 0 -1.7px; } /* Overpass sets text ~1.7 px high in its line: icons on the text's cap-height centre, measured in Chrome (Oct 12) */
  .k-alert { height: 10px; background: var(--flag-blue); opacity: 0.18; }
  .k-flag { height: 1.5px; background: var(--asphalt); }
  .k-out { height: 8px; border-radius: 4px; background: var(--star-red); }
  .k-whisker { height: 1.5px; background: var(--star-red); }
</style>

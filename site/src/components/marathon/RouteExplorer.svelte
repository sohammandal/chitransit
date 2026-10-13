<script lang="ts">
  // "What happened to your route": pick one of the rerouted routes (or tap its row in the timeline:
  // the pick is shared through pickedRoute), read what happened to it, and see it alone on the
  // reroutes map. ?route=22 in the address picks it on load, so a rider can link their route.
  import { onMount, tick } from "svelte";
  import ReroutesMap from "./ReroutesMap.svelte";
  import { clock, load, pickedRoute, type Reroutes, type Timeline } from "../../lib/marathon";
  import { routeUrl } from "../../lib/links";

  type Step = { at: number | null; text: string; tone: "cta" | "flag" | "stops" };
  const DIRS: Record<string, string> = { N: "Northbound", S: "Southbound", E: "Eastbound", W: "Westbound" };

  let root: HTMLElement;
  let box: HTMLElement;
  let button: HTMLButtonElement;
  let list: HTMLUListElement;
  let tl = $state.raw<Timeline | null>(null);
  let rr = $state.raw<Reroutes | null>(null);
  let failed = $state(false);
  let open = $state(false); // the picker's list...
  let up = $state(false); // ...above the button or below, where there's more room
  let room = $state(0); // ...and how much, px
  let active = $state(0); // the row the arrow keys are on
  let typed = ""; // what was just typed, to jump to a route: "22", "cl" (Clark)
  let typedAt = 0;

  const num = (rt: string) => parseInt(rt) || 999; // J14 after the numbers
  const tag = (rt: string) => (/^\d/.test(rt) ? `#${rt}` : rt); // "#22", "J14"
  const day = (m: number) => Math.floor(m / 1440); // days after Oct 11 (a multi-day alert's ends)
  const routes = $derived(tl ? [...tl.routes].sort((a, b) => num(a.rt) - num(b.rt) || a.rt.localeCompare(b.rt)) : []);
  const r = $derived(tl?.routes.find((x) => x.rt === $pickedRoute) ?? null);
  // the picker's rows: all routes, then each
  const rows = $derived([
    { rt: "", name: tl ? `All ${routes.length} routes` : "All routes" },
    ...routes.map((x) => ({ rt: x.rt, name: x.name.trim() })),
  ]);
  // per direction: [cut off, stops it serves at midday on a normal Sunday]
  const dirs = $derived(rr && r ? rr.routes.filter((d) => d.rt === r.rt) : []);
  const served = $derived(dirs.reduce((t, d) => t + d.stops[1], 0));
  // "78 of its 152 stops cut off" (one direction rerouted: "7 of its 66 eastbound stops")
  const cut = $derived(
    !r ? ["", ""] : !served ? [`${r.cut}`, "stops cut off"] : r.cut === served ? [`All ${r.cut}`, "of its stops cut off"]
      : [`${r.cut}`, `of its ${served}${dirs.length === 1 ? ` ${DIRS[dirs[0].dir].toLowerCase()}` : ""} stops cut off`],
  );

  // Its day in order: CTA's alerts, its detour flag, and its cut-off stops losing and getting back a bus
  const events = $derived.by((): Step[] => {
    if (!tl || !r) return [];
    const bus = `${tag(r.rt)} bus`; // its own: another route's bus may have served the same stop
    // its own alerts: the race-day one (6:30 AM to 4 PM) and, for five routes, a multi-day one
    // (Oct 8 to Oct 11 8 PM or Oct 12 6 AM: set-up and clean-up, Balbo to Roosevelt)
    const ev: Step[] = (r.alerts?.length ? r.alerts : [tl.alerts]).flatMap(([a, b]) => {
      const what = day(a) || day(b) ? "CTA's multi-day Marathon alert" : "CTA's race-day alert";
      return [{ at: a, text: `${what} begins`, tone: "cta" as const }, { at: b, text: `${what} ends`, tone: "cta" as const }];
    });
    ev.push({ at: r.flag[0], text: "Detour flag on in Bus Tracker", tone: "flag" });
    if (r.flag[1] != null) ev.push({ at: r.flag[1], text: "Detour flag off", tone: "flag" });
    if (r.back) {
      const all = !r.never; // "all" only when every one of them had a bus again
      if (r.back[1] != null) ev.push({ at: r.back[1], text: `A ${bus} again at ${all && r.back[2] === r.back[1] ? "all" : "half"} its cut-off stops`, tone: "stops" });
      if (all && r.back[2] !== r.back[1]) ev.push({ at: r.back[2], text: `A ${bus} again at all its cut-off stops`, tone: "stops" });
    }
    // when they lost their buses: the median of each stop's first missed bus (reroutes.json)
    if (rr) {
      const missed = rr.stops.filter((s) => s[0] === r.rt && s[5] != null).map((s) => s[5]!).sort((a, b) => a - b);
      if (missed.length) ev.push({ at: missed[Math.ceil(missed.length / 2) - 1], text: `First missed ${bus} at half its cut-off stops`, tone: "stops" });
    }
    ev.sort((a, b) => a.at! - b.at!);
    if (r.flag[1] == null) ev.push({ at: null, text: "Detour flag still on the next morning", tone: "flag" });
    if (!r.back) ev.push({ at: null, text: `No ${bus} again at its cut-off stops that day`, tone: "stops" });
    return ev;
  });

  // The address names the picked route, to link it (replaceState: Back still leaves the page)
  function address(rt: string) {
    const u = new URL(location.href);
    if (rt) u.searchParams.set("route", rt);
    else u.searchParams.delete("route");
    if (u.href !== location.href) history.replaceState(history.state, "", u);
  }

  // The picker's list, like the stop finder's: below the button, or above where there's more room
  // (a phone scrolled so the button is low), opened on the picked route
  async function show() {
    const v = visualViewport, b = button.getBoundingClientRect();
    const above = b.top - (v?.offsetTop ?? 0), below = (v ? v.offsetTop + v.height : innerHeight) - b.bottom;
    up = below < 300 && above > below;
    room = Math.min(440, (up ? above : below) - 12);
    active = Math.max(0, rows.findIndex((o) => o.rt === $pickedRoute));
    open = true;
    await tick();
    reveal(true);
  }
  function choose(rt: string) {
    pickedRoute.set(rt);
    open = false;
  }
  // the active row in the list's view (in its middle when the list opens)
  function reveal(middle = false) {
    const li = list?.children[active] as HTMLElement | undefined;
    if (!li) return;
    if (middle) list.scrollTop = li.offsetTop - (list.clientHeight - li.offsetHeight) / 2;
    else if (li.offsetTop < list.scrollTop) list.scrollTop = li.offsetTop - 4;
    else if (li.offsetTop + li.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = li.offsetTop + li.offsetHeight - list.clientHeight + 4;
  }

  const STEP: Record<string, number> = { ArrowDown: 1, ArrowUp: -1, PageDown: 8, PageUp: -8, Home: -999, End: 999 };
  function key(e: KeyboardEvent) {
    if (e.key.length > 1) typed = "";
    if (e.key in STEP) {
      e.preventDefault();
      if (!open) return void show();
      active = Math.min(rows.length - 1, Math.max(0, active + STEP[e.key]));
      reveal();
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault(); // the button's own click would close it again
      if (open) choose(rows[active].rt);
      else show();
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      open = false;
    } else if (e.key === "Tab") open = false;
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // typing jumps to a route by its number or a word of its name
      typed = (e.timeStamp - typedAt < 800 ? typed : "") + e.key.toLowerCase();
      typedAt = e.timeStamp;
      const i = rows.findIndex((o) => o.rt && (o.rt.toLowerCase().startsWith(typed) || o.name.toLowerCase().split(/[^a-z0-9]+/).some((w) => w.startsWith(typed))));
      if (i < 0) return;
      if (!open) show().then(() => { active = i; reveal(true); });
      else { active = i; reveal(); }
    }
  }

  // the row under the mouse when it moves: not when the list scrolls under a resting pointer (the
  // arrow keys' row went back to it, Oct 12)
  let pointer = "";
  function point(e: PointerEvent, i: number) {
    const at = `${e.screenX},${e.screenY}`;
    if (at !== pointer) [pointer, active] = [at, i];
  }

  // a press anywhere else closes the list (a phone's tap doesn't focus the button, so no blur)
  $effect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!box.contains(e.target as Node)) open = false; };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  });

  onMount(() => {
    let unsub = () => {};
    load<Timeline>("timeline.json").then((d) => {
      tl = d;
      const want = new URLSearchParams(location.search).get("route")?.toUpperCase();
      if (want && d.routes.some((x) => x.rt === want)) {
        pickedRoute.set(want);
        // a shared link opens here; a reload keeps the reader's own place
        const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
        if (nav?.type === "navigate") requestAnimationFrame(() => root.scrollIntoView());
      }
      unsub = pickedRoute.subscribe(address); // picks here and in the timeline alike
    }, () => (failed = true));
    return () => unsub();
  });

  // the card's per-direction numbers come with the map's data: fetched once a route is picked
  $effect(() => {
    if ($pickedRoute && !rr) load<Reroutes>("reroutes.json").then((d) => (rr = d), () => {});
  });
</script>

<div class="explorer" id="route-explorer" bind:this={root}>
  <div class="grid">
    <div class="side">
      <div class="label" id="route-label">Your route</div>
      <!-- the page's own list, not the browser's: a long system menu on a computer (Oct 12) -->
      <div class="select" bind:this={box} onfocusout={(e) => { if (!box.contains(e.relatedTarget as Node | null)) open = false; }}>
        <button id="route-pick" class="pick" bind:this={button} type="button" role="combobox" aria-labelledby="route-label"
          aria-haspopup="listbox" aria-controls="route-list" aria-expanded={open} aria-activedescendant={open ? `route-opt-${active}` : undefined}
          disabled={!tl} onclick={() => (open ? (open = false) : show())} onkeydown={key} onkeyup={(e) => { if (e.key === " ") e.preventDefault(); }}>
          <span>{r ? `${tag(r.rt)} ${r.name.trim()}` : rows[0].name}</span>
        </button>
        <!-- a press on a row doesn't take the focus from the button -->
        <ul id="route-list" role="listbox" aria-labelledby="route-label" class="list" class:up hidden={!open} bind:this={list}
          style:max-height="{room}px" onmousedown={(e) => e.preventDefault()}>
          {#each rows as o, i (o.rt)}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <li id="route-opt-{i}" role="option" tabindex="-1" aria-selected={o.rt === $pickedRoute} aria-label={o.rt ? `${tag(o.rt)} ${o.name}` : o.name}
              class:active={i === active} class:all={!o.rt} onpointermove={(e) => point(e, i)} onclick={() => choose(o.rt)}>
              <span>{#if o.rt}<b>{tag(o.rt)}</b>{/if}{o.name}</span>
              {#if o.rt === $pickedRoute}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>{/if}
            </li>
          {/each}
        </ul>
      </div>
      {#if failed}
        <p class="hint">The routes didn't load. Reload the page to try again</p>
      {:else if r}
        <div class="card">
          <p class="title">{tag(r.rt)} {r.name.trim()}</p>
          {#if r.rides}<p class="rides">About {(Math.round(r.rides / 100) * 100).toLocaleString("en-US")} rides on an average Sunday</p>{/if}
          <p class="cut"><strong>{cut[0]}</strong> {cut[1]}</p>
          {#if dirs.length > 1}
            <ul class="dirs">
              {#each dirs as d (d.dir)}
                <li><span>{DIRS[d.dir]}</span><span class="bar"><i style="width:{(100 * d.stops[0]) / (d.stops[1] || 1)}%"></i></span><span>{d.stops[0]} of {d.stops[1]}</span></li>
              {/each}
            </ul>
          {/if}
          <p class="def">Cut off: no {tag(r.rt)} bus from 10 AM to noon, where the two Sundays before had 2 or more</p>
          <ol class="day">
            {#each events as e}
              <li class={e.tone}><time>{e.at == null ? "" : clock(((e.at % 1440) + 1440) % 1440)}{#if e.at != null && day(e.at)}<small>Oct {11 + day(e.at)}</small>{/if}</time><span>{e.text}</span></li>
            {/each}
          </ol>
          <p class="hint">Tap a ring on the map for one stop's hours</p>
          <a class="go" href={routeUrl(r.rt)}>See the {tag(r.rt)} live now &rarr;</a>
        </div>
      {:else}
        <!-- from the start, so nothing below moves when the routes come -->
        <p class="hint">All {tl ? `${routes.length} ` : ""}rerouted routes are on the map. Pick yours to see it alone</p>
      {/if}
    </div>
    <ReroutesMap />
  </div>
</div>

<style>
  .explorer { container-type: inline-size; scroll-margin-top: 12px; }
  /* text keeps a 16 px gutter when the page lays this edge to edge (a phone's wide figure) */
  .side { padding: 0 max(0px, calc(16px - (100vw - 100%) / 2)) 14px; }
  @container (min-width: 760px) {
    .grid { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 24px; align-items: start; }
    .side { padding: 0; }
  }
  .label { margin: 0 0 6px; font-size: 13px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--fog); }
  .select { position: relative; }
  .select::after {
    /* the "v" (all of it below its box's centre) on the text's cap-height centre, measured in Chrome */
    content: ""; position: absolute; right: 18px; top: 50%; width: 9px; height: 9px; margin-top: -7.7px; pointer-events: none;
    border-right: 2.5px solid var(--asphalt); border-bottom: 2.5px solid var(--asphalt); transform: rotate(45deg);
  }
  .pick {
    display: flex; align-items: center; width: 100%; min-height: 54px; padding: 11.8px 44px 8.2px 14px; /* text centred: Overpass sits ~1.8 px high */
    border-radius: 12px; cursor: pointer; text-align: left;
    font: 700 19px/1.2 var(--font); color: var(--asphalt); background: var(--surface); border: 2px solid var(--flag-blue);
  }
  .pick span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pick:disabled { border-color: var(--rule); cursor: default; }
  /* the list: the stop finder's (Timelapse.svelte) */
  .list {
    position: absolute; z-index: 10; left: 0; right: 0; top: calc(100% + 6px); margin: 0; padding: 4px; list-style: none;
    background: var(--surface); border-radius: 12px; box-shadow: var(--shadow); overflow-y: auto; overscroll-behavior: contain;
  }
  .list.up { top: auto; bottom: calc(100% + 6px); }
  .list li { display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 6.75px 10px 3.25px; /* Overpass sits high: nudge */ border-radius: 10px; font-size: 15px; line-height: 1.3; cursor: pointer; }
  .list li.active { background: var(--concrete); }
  .list li.all { position: relative; margin-bottom: 5px; }
  .list li.all::after { content: ""; position: absolute; left: 10px; right: 10px; bottom: -3px; height: 1px; background: var(--rule); }
  /* the number in a column; a long name wraps under its own start */
  .list span { flex: 1; min-width: 0; padding-left: 46px; text-indent: -46px; }
  .list .all span { padding-left: 0; text-indent: 0; }
  .list b { display: inline-block; width: 46px; text-indent: 0; font-weight: 800; }
  .list svg { flex: none; width: 18px; height: 18px; translate: 0 -1.75px; fill: none; stroke: var(--flag-blue); stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
  .card { margin-top: 14px; }
  .card p { margin: 0; }
  .card .rides { margin-top: 2px; }
  .card .cut { margin-top: 12px; }
  .card .def { margin-top: 6px; }
  .card .hint, .hint { margin: 12px 0 0; }
  .title { font-size: 20px; font-weight: 800; line-height: 1.25; }
  .go { display: inline-flex; align-items: center; min-height: 44px; margin-top: 6px; font-weight: 700; font-size: 16px; color: var(--flag-blue); text-decoration: none; }
  .go:hover { text-decoration: underline; }
  .rides { font-size: 15px; line-height: 1.4; color: var(--fog); }
  .cut { font-size: 18px; line-height: 1.3; }
  .cut strong { color: var(--star-red); font-size: 26px; font-weight: 800; }
  .dirs { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 4px; font-size: 14px; }
  .dirs li { display: grid; grid-template-columns: 92px minmax(0, 1fr) auto; align-items: center; gap: 10px; }
  .bar { height: 8px; border-radius: 4px; background: var(--surface2); overflow: hidden; translate: 0 -1.55px; } /* Overpass sets text ~1.7 px high in its line: icons on the text's cap-height centre, measured in Chrome (Oct 12) */
  .bar i { display: block; height: 100%; background: var(--star-red); }
  .def { font-size: 13px; line-height: 1.4; color: var(--fog); }
  .day { list-style: none; margin: 14px 0 0; padding: 0; position: relative; font-size: 15px; line-height: 1.35; }
  .day::before { content: ""; position: absolute; left: 81px; top: 10px; bottom: 10px; width: 2px; background: var(--rule); }
  .day li { display: grid; grid-template-columns: 70px minmax(0, 1fr); gap: 24px; padding: 4px 0; position: relative; }
  .day li::before {
    content: ""; position: absolute; left: 77px; top: 7px; width: 10px; height: 10px; border-radius: 50%;
    background: var(--c); box-shadow: 0 0 0 2px var(--concrete);
  }
  .day time { text-align: right; font-weight: 700; white-space: nowrap; }
  .day time small { display: block; font-size: 12px; font-weight: 400; color: var(--fog); }
  .cta { --c: var(--flag-blue); }
  .cta span { color: var(--fog); }
  .flag { --c: var(--asphalt); }
  .stops { --c: var(--star-red); }
  .hint { font-size: 14px; line-height: 1.4; color: var(--fog); }
</style>

<script lang="ts">
  // Marathon day as it happened, ten minutes a frame: every stop that normally sees buses, red
  // while it was in a stretch of 90 minutes or more without its usual buses; the course, and
  // where the runners were on it. Plays once when the map is scrolled into view (not under
  // reduced motion: then it opens at the worst moment); the slider scrubs, the moments jump.
  // Find a stop: the map goes there and a strip under it shows that stop's day.
  import { onMount } from "svelte";
  import * as maplibregl from "maplibre-gl";
  import "maplibre-gl/dist/maplibre-gl.css";
  import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
  import { clock, load, slice, type Course, type StopFrames } from "../../lib/marathon";

  maplibregl.setWorkerUrl(workerUrl);

  const STYLES = { light: "https://tiles.openfreemap.org/styles/positron", dark: "https://tiles.openfreemap.org/styles/dark" };
  const FPS = 6; // ten minutes of the day each sixth of a second: the race morning in ~10 s
  // Moments that are facts, not data (minutes after midnight): the city closed the course's
  // streets, the race started, CTA's race-day alerts expired (every route's ran 6:30 AM-4 PM; five
  // routes also had multi-day ones, so not just "alerts end")
  const FIXED: [number, string][] = [[360, "course closes"], [440, "race starts"], [960, "race-day alerts end"]];
  const ZOOM = 15; // a picked stop: its corner and a few blocks around
  const WAY: Record<string, string> = { N: "Northbound", S: "Southbound", E: "Eastbound", W: "Westbound" };
  const SIDE = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"];

  /** Stops of one name (a corner's two directions, a station's bays): found as one place. */
  type Place = { name: string; words: string[]; ids: number[] };
  type Stop = StopFrames["stops"][number];
  /** A place's stops with the same day (a station's bays) as one row: its strip, sentence and label. */
  type Row = { s: string; says: string; rds: string[]; travel: string; ids: number[]; label: string };

  let container: HTMLDivElement;
  let data = $state.raw<StopFrames | null>(null);
  let course = $state.raw<Course | null>(null);
  let frame = $state(0);
  let playing = $state(false);
  let failed = $state(false);
  let query = $state("");
  let open = $state(false); // the suggestions...
  let up = $state(true); // ...above the box or below, where there's more room (a phone's keyboard takes the bottom)
  let room = $state(0); // ...and how much, px
  let active = $state(-1); // the suggestion the arrow keys are on
  let picked = $state.raw<Place | null>(null);
  let map: maplibregl.Map | null = null;
  let ready = false;

  const time = $derived(data ? clock(data.start + frame * data.step) : "");
  const dark = $derived(data?.dark[frame] ?? 0);
  const peak = $derived(data ? data.dark.indexOf(Math.max(...data.dark)) : 0);

  // The moments to jump to ([frame, what]): the fixed ones, the peak, and the first frame after it
  // with fewer dark stops than normal Sundays show
  const moments = $derived.by(() => {
    const d = data;
    if (!d) return [];
    const all: [number, string][] = FIXED.map(([m, what]) => [Math.round((m - d.start) / d.step), what]);
    all.push([peak, "peak"], [d.dark.findIndex((v, i) => i > peak && v < d.noise[i]), "back to normal"]);
    return all.filter(([i]) => i >= 0 && i < d.frames).sort((a, b) => a[0] - b[0]);
  });

  const words = (s: string) => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const places = $derived.by(() => {
    const by = new Map<string, Place>();
    data?.stops.forEach(([, , raw], k) => {
      const name = raw.trim().replace(/\s+/g, " ");
      const p = by.get(name.toLowerCase());
      if (p) p.ids.push(k);
      else by.set(name.toLowerCase(), { name, words: words(name), ids: [k] });
    });
    return [...by.values()];
  });

  // Each typed word starts a word of the name, in any order ("randolph state": State & Randolph);
  // names that start with the first word first
  function find(q: string): Place[] {
    const qs = words(q);
    const late = (p: Place) => +!p.words[0].startsWith(qs[0]);
    return qs.length
      ? places.filter((p) => qs.every((w) => p.words.some((x) => x.startsWith(w))))
        .sort((a, b) => late(a) - late(b) || a.name.localeCompare(b.name))
        .slice(0, 8)
      : [];
  }
  const found = $derived(find(query));
  const listed = $derived(open && query.trim() !== "");

  function mid(ids: number[]) {
    const [x, y] = ids.reduce(([x, y], k) => [x + data!.stops[k][0], y + data!.stops[k][1]], [0, 0]);
    return { lon: x / ids.length, lat: y / ids.length };
  }

  // The picked place's rows: one per stop, those with the same day and sentence as one
  const rows = $derived.by(() => {
    const d = data, p = picked;
    if (!d || !p) return [];
    const rs: Row[] = [];
    for (const k of p.ids) {
      const st = d.stops[k], line = says(st), rds = st[4] ? st[4].split(",") : [];
      const r = rs.find((x) => x.s === st[3] && x.says === line);
      if (r) { r.ids.push(k); r.rds.push(...rds); r.travel += st[5]; }
      else rs.push({ s: st[3], says: line, rds, travel: st[5], ids: [k], label: "" });
    }
    if (rs.length > 1) label(rs);
    return rs.sort((a, b) => a.label.localeCompare(b.label, "en", { numeric: true }));
  });
  // What they come to: one sentence, or one a row when they differ
  const told = $derived(rows.every((r) => r.says === rows[0].says) ? rows.slice(0, 1).map((r) => r.says) : rows.map((r) => `${r.label}: ${r.says}`));

  const tag = (rt: string) => (/^\d/.test(rt) ? `#${rt}` : rt); // "#22", "J14"
  const rtOf = (rd: string) => rd.slice(0, -1); // a route-direction "22N": route 22, CTA's Northbound
  const byRoute = (a: string, b: string) => (parseInt(a) || 1e4) - (parseInt(b) || 1e4) || a.localeCompare(b);
  function uniq<T>(xs: T[]): T[] {
    return [...new Set(xs)];
  }
  // "#22", "#22 or #36", "#10, #29 or #146"
  const list = (xs: string[], and: string) => (xs.length < 3 ? xs.join(` ${and} `) : `${xs.slice(0, -1).join(", ")} ${and} ${xs[xs.length - 1]}`);
  const t = (m: number) => clock(m).replace(" ", "\u00a0"); // "5:13 AM" on one line
  // a last bus in the small hours was Saturday night's: "until 6:30 PM (the last came at 12:05 AM)",
  // not "from 12:05 AM", which reads as hours of missed buses (95 stops, Oct 12)
  const span = (a: number | null, b: number | null) =>
    a != null && a < 240 ? `${b == null ? "all day" : `until ${t(b)}`} (the last came at ${t(a)})`
      : a == null ? (b == null ? "all day" : `until ${t(b)}`) : b == null ? `after ${t(a)}` : `from ${t(a)} to ${t(b)}`;

  // A stop's day in a sentence, from its own usual routes' buses (stops.json's day), never from the
  // frames: a frame blurs times to 10 minutes, and another route's bus ends a red run while its own
  // routes are still out (Oct 12 check: 42 of 638 sentences ended an hour or more early). The
  // stretch is its longest without any of them; then the routes out longer, and other routes'
  // buses that came by meanwhile: "No #29 or #146 bus from 5:07 AM to 5:36 PM (J14 buses came by
  // 7:41 AM–5:31 PM); #146 not back until 6:27 PM"
  function says([, , , s, routes, , day]: Stop): string {
    if (s.includes("u")) return "Shuttle stop: can't tell";
    // '.': hours it has no buses on a normal Sunday either (14227 Washington & State: #22's nights)
    const usual = s.includes(".") ? "Buses as usual, at the hours it normally has them" : "Buses as usual all day";
    if (!day) return usual;
    const [from, to, n, cut, came] = day;
    // red on the strip, but buses of its routes came close (parked at it, or passing its way):
    // never "as usual", never "no bus"
    if (n < 0) return came.length ? `Can't tell: ${cameBy(came).replace(" came by", " came close")}, maybe without stopping` : "Can't tell";
    const rds = routes ? routes.split(",") : [];
    // no route stops here on both normal Sundays: detour buses did that day
    if (!rds.length) return ["Not a regular Sunday stop", ...(came.length ? [cameBy(came)] : [])].join("; ");
    // a route that stops here both ways is named with its direction ("#60 eastbound")
    const named = (rd: string) => tag(rtOf(rd)) + (rds.filter((x) => rtOf(x) === rtOf(rd)).length > 1 ? ` ${WAY[rd.slice(-1)].toLowerCase()}` : "");
    if (!n) {
      // always a bus of its routes, but not of each: each one's longest stretch
      const each = cut.map(([rd, a, b]) => `no ${named(rd)} bus ${span(a, b)}`).join("; ");
      return each ? each[0].toUpperCase() + each.slice(1) : usual;
    }
    const rts = uniq(rds.map(rtOf)).sort(byRoute).map(tag);
    // routes out longer: gone sooner, then back later in the order they came back ("#124 not back
    // until 5:26 PM, #60 until 6:19 PM"), then those not back that day
    const back = cut.filter(([, a, b]) => a == null && b != null), gone = cut.filter(([, a, b]) => a == null && b == null);
    const longer = [
      ...cut.filter(([, a]) => a != null).map(([rd, a, b]) => `no ${named(rd)} bus ${span(a, b)}`),
      ...back.map(([rd, , b], i) => `${named(rd)} ${i ? "" : "not back "}until ${t(b!)}`),
      ...(gone.length ? [`${list(gone.map(([rd]) => named(rd)), "and")} not back that day`] : []),
    ];
    return `${rts.length > 4 ? `No bus of its ${rts.length} routes` : `No ${list(rts, "or")} bus`} ${span(from, to)}`
      + (n > 1 ? `, the longest of ${n} stretches` : "")
      + (came.length ? ` (${cameBy(came)})` : "")
      + (longer.length ? `; ${longer.join(", ")}` : "");
  }

  // Other routes' buses: "a J14 bus came by at 11:02 AM", "#152 buses came by 7:52 AM–1:09 PM"
  function cameBy(came: NonNullable<Stop[6]>[4]): string {
    if (came.length > 1) return `${list(came.map(([rt]) => tag(rt)), "and")} buses came by`;
    const [[rt, a, b, k]] = came;
    return k > 1 ? `${tag(rt)} buses came by ${t(a)}\u2013${t(b)}` : `a ${tag(rt)} bus came by at ${t(a)}`;
  }

  // Rows are labelled by where their buses go: CTA's direction where it's also the way they go
  // there ("Northbound"), else with the routes ("#60 Westbound": it goes south on Racine); the
  // routes alone when CTA's directions differ. If that leaves two alike: the way they go, then each
  // route's direction (the first with the fewest alike); then where each still alike, or without
  // routes of its own, stands ("north stop"). Never a stop's place alone where it has routes:
  // "North" reads as northbound, and 321 of 813 pairs had it backwards (Oct 12)
  function label(rs: Row[]) {
    const f = rs.map((r) => {
      const cta = uniq(r.rds.map((x) => x.slice(-1))), way = uniq([...r.travel]);
      const rds = uniq(r.rds).sort((a, b) => byRoute(rtOf(a), rtOf(b)) || a.localeCompare(b));
      return { rds, rts: list(uniq(rds.map(rtOf)).map(tag), "and"), cta: cta.length === 1 ? cta[0] : "", way: way.length === 1 ? way[0] : "" };
    });
    type F = (typeof f)[number];
    const schemes = [
      (x: F) => (x.cta && x.cta === x.way ? WAY[x.cta] : x.cta ? `${x.rts} ${WAY[x.cta]}` : x.rts),
      (x: F) => (x.way ? WAY[x.way] : ""),
      (x: F) => list(x.rds.map((rd) => `${tag(rtOf(rd))} ${WAY[rd.slice(-1)]}`), "and"),
    ];
    const alikeIn = (ls: string[]) => ls.filter((l, i) => f[i].rts && (!l || ls.indexOf(l) !== ls.lastIndexOf(l))).length;
    let ls: string[] = [];
    for (const scheme of schemes) {
      const next = f.map((x) => (x.rts ? scheme(x) : ""));
      if (!ls.length || alikeIn(next) < alikeIn(ls)) ls = next;
      if (!alikeIn(ls)) break;
    }
    rs.forEach((r, i) => {
      const alike = rs.filter((_, j) => ls[j] === ls[i]);
      if (ls[i] && alike.length === 1) return void (r.label = ls[i]);
      // where it stands against the others alike (or, alone without routes, all the others)
      const of = alike.length > 1 ? alike : rs, c = mid(of.flatMap((x) => x.ids)), p = mid(r.ids);
      const dx = (p.lon - c.lon) * Math.cos((c.lat * Math.PI) / 180), dy = p.lat - c.lat;
      const side = of.length > 2 ? SIDE[Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) & 7]
        : Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "east" : "west") : dy > 0 ? "north" : "south";
      r.label = ls[i] ? `${ls[i]}, ${side} stop` : `${side[0].toUpperCase()}${side.slice(1)} stop`;
    });
  }

  // A day as runs of one state, a rect each ('.', no service then: the bare track)
  const runs = (s: string) => [...s.matchAll(/([^.])\1*/g)].map((m) => [m.index, m[0].length, m[1]] as const);

  function colors() {
    const css = getComputedStyle(document.documentElement);
    const v = (name: string) => css.getPropertyValue(name).trim();
    return { red: v("--star-red"), blue: v("--flag-blue"), ink: v("--asphalt"), fog: v("--fog"), amber: v("--signal-amber"), ground: v("--concrete") };
  }

  function stopsAt(i: number): GeoJSON.FeatureCollection {
    return {
      type: "FeatureCollection",
      features: data!.stops.flatMap(([lon, lat, , states], k) =>
        states[i] === "." ? [] : [{ type: "Feature", id: k, properties: { s: states[i] }, geometry: { type: "Point", coordinates: [lon, lat] } }],
      ),
    };
  }

  // The stretch of the course the runners covered at this frame: from the last runner to the first
  function fieldAt(i: number): GeoJSON.FeatureCollection {
    const span = data?.field?.[i];
    if (!course || !span) return { type: "FeatureCollection", features: [] };
    return { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: slice(course, span[0], span[1]) } }] };
  }

  function ring(): GeoJSON.FeatureCollection {
    return {
      type: "FeatureCollection",
      features: (picked?.ids ?? []).map((k) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: data!.stops[k].slice(0, 2) as number[] } })),
    };
  }

  function addLayers() {
    if (!map || !data || !course) return;
    const c = colors();
    map.addSource("course", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: course.line } } });
    map.addSource("field", { type: "geojson", data: fieldAt(frame) });
    map.addSource("stops", { type: "geojson", data: stopsAt(frame) });
    map.addSource("pick", { type: "geojson", data: ring() });
    map.addLayer({ id: "course-casing", type: "line", source: "course", paint: { "line-color": c.ground, "line-width": 6 }, layout: { "line-join": "round" } });
    map.addLayer({ id: "course", type: "line", source: "course", paint: { "line-color": c.ink, "line-width": 2.5 }, layout: { "line-join": "round" } });
    map.addLayer({ id: "field", type: "line", source: "field", paint: { "line-color": c.amber, "line-width": 7, "line-opacity": 0.85 }, layout: { "line-join": "round", "line-cap": "round" } });
    // dark: red; partly dark (a route cut, another bus came): faint red; can't tell: a grey ring; served: blue
    const state = (d: unknown, p: unknown, u: unknown, s: unknown) => ["match", ["get", "s"], "d", d, "p", p, "u", u, s] as maplibregl.ExpressionSpecification;
    map.addLayer({
      id: "stops", type: "circle", source: "stops",
      paint: {
        "circle-color": state(c.red, c.red, c.ground, c.blue),
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, state(2.6, 2, 2, 1.3), 14, state(6, 4.5, 4.5, 3.5)],
        "circle-opacity": state(0.95, 0.4, 0, 0.4),
        "circle-stroke-color": c.fog,
        "circle-stroke-width": state(0, 0, 1.2, 0),
      },
    });
    // the picked stop: an ink ring, the ground either side of it so it reads over any dots
    map.addLayer({ id: "pick-halo", type: "circle", source: "pick", paint: { "circle-radius": 8, "circle-opacity": 0, "circle-stroke-color": c.ground, "circle-stroke-width": 7, "circle-stroke-opacity": 0.85 } });
    map.addLayer({ id: "pick", type: "circle", source: "pick", paint: { "circle-radius": 10, "circle-opacity": 0, "circle-stroke-color": c.ink, "circle-stroke-width": 2.5 } });
    ready = true;
  }

  function show(i: number) {
    frame = i;
    if (!ready || !map) return;
    (map.getSource("stops") as maplibregl.GeoJSONSource).setData(stopsAt(i));
    (map.getSource("field") as maplibregl.GeoJSONSource).setData(fieldAt(i));
  }

  let timer: ReturnType<typeof setInterval> | undefined;
  function play() {
    if (!data) return;
    if (frame >= data.frames - 1) show(0);
    playing = true;
    clearInterval(timer);
    timer = setInterval(() => {
      if (frame >= data!.frames - 1) return stop();
      show(frame + 1);
    }, 1000 / FPS);
  }
  function stop() {
    playing = false;
    clearInterval(timer);
  }
  function jump(i: number) {
    stop();
    show(i);
  }

  // A place's day (null: none): the map goes there, and the address keeps it so the page can be
  // shared open on it
  function pick(p: Place | null) {
    const had = picked;
    picked = p;
    query = p?.name ?? "";
    open = false;
    if (ready) (map!.getSource("pick") as maplibregl.GeoJSONSource).setData(ring());
    const c = p && mid(p.ids);
    if (c && map) map.easeTo({ center: [c.lon, c.lat], zoom: Math.max(ZOOM, map.getZoom()) });
    if (!p && had) map?.fitBounds(data!.bounds, { padding: 12 }); // back over the whole course
    const u = new URL(location.href);
    if (p) u.searchParams.set("stop", p.name);
    else u.searchParams.delete("stop");
    history.replaceState(history.state, "", u);
  }
  function choose(p: Place) {
    pick(p);
    (document.activeElement as HTMLElement | null)?.blur(); // phones: close the keyboard
  }

  function typed(e: Event) {
    open = true;
    active = -1;
    const v = visualViewport, r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const above = r.top - (v?.offsetTop ?? 0), below = (v ? v.offsetTop + v.height : innerHeight) - r.bottom;
    up = above > below;
    room = Math.max(above, below) - 12;
  }

  function key(e: KeyboardEvent) {
    const n = found.length;
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && n) {
      e.preventDefault();
      if (!listed) typed(e);
      active = e.key === "ArrowDown" ? (active + 1) % n : (active <= 0 ? n : active) - 1;
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (listed && n) choose(found[Math.max(active, 0)]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (listed) open = false;
      else query = picked?.name ?? "";
    }
  }

  // "?at=11:20" (24-hour): a link to a moment of the day opens there, still
  function linked(d: StopFrames): number | null {
    const m = /^(\d{1,2}):(\d{2})$/.exec(new URLSearchParams(location.search).get("at") ?? "");
    if (!m) return null;
    const i = Math.round((+m[1] * 60 + +m[2] - d.start) / d.step);
    return Math.min(Math.max(i, 0), d.frames - 1);
  }

  onMount(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    Promise.all([load<StopFrames>("stops.json"), load<Course>("course.json")]).then(([d, cr]) => {
      data = d;
      course = cr;
      const at = linked(d);
      frame = at ?? (reduce ? peak : 0);
      const still = reduce || at != null;
      // "?stop=State & Randolph": open on that stop (its name, or the best match for it)
      const q = new URLSearchParams(location.search).get("stop")?.trim().toLowerCase();
      if (q) picked = places.find((p) => p.name.toLowerCase() === q) ?? find(q)[0] ?? null;
      query = picked?.name ?? "";
      const c = picked && mid(picked.ids);
      map = new maplibregl.Map({
        container, style: scheme.matches ? STYLES.dark : STYLES.light,
        ...(c ? { center: [c.lon, c.lat] as [number, number], zoom: ZOOM } : { bounds: d.bounds, fitBoundsOptions: { padding: 12 } }),
        attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false, cooperativeGestures: true,
      });
      map.touchZoomRotate.disableRotation();
      map.on("load", () => container.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show"));
      const w = window as unknown as { marathonMap?: maplibregl.Map; __e2e?: boolean };
      if (import.meta.env.DEV || w.__e2e) w.marathonMap = map;
      map.on("style.load", () => { ready = false; addLayers(); show(frame); });
      scheme.addEventListener("change", () => map?.setStyle(scheme.matches ? STYLES.dark : STYLES.light));
      if (!still) {
        // once, the first time most of the map is on screen
        const io = new IntersectionObserver(([e]) => {
          if (e.isIntersecting) { io.disconnect(); map!.once("idle", play); if (map!.loaded()) play(); }
        }, { threshold: 0.6 });
        io.observe(container);
      }
    }).catch(() => (failed = true));
    return () => { stop(); map?.remove(); };
  });

  // The day's count as a filled line, scaled to the busiest frame (viewBox: frames x 100)
  function spark(values: number[]): string {
    const top = Math.max(1, ...data!.dark);
    return values.map((v, i) => `${i ? "L" : "M"}${i},${(100 - (98 * v) / top).toFixed(1)}`).join("");
  }
</script>

<figure class="timelapse">
  <div class="map" bind:this={container} role="img" aria-label="Map of the bus stops near the Marathon course through the day: stops without their usual buses shown in red, most of them near the course"></div>
  {#if failed}
    <p class="fail">The map's data didn't load. Reload the page to try again</p>
  {:else if data}
    <div class="below">
      <div class="find">
        <svg class="glass" viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5 21 21" /></svg>
        <input bind:value={query} type="search" role="combobox" aria-label="Find your stop"
          placeholder="Find your stop, e.g. State & Randolph" autocomplete="off" autocorrect="off" spellcheck="false" enterkeyhint="search"
          aria-autocomplete="list" aria-controls="tl-stops" aria-expanded={listed} aria-activedescendant={listed && active >= 0 ? `tl-stop-${active}` : undefined}
          oninput={typed} onkeydown={key} onfocus={(e) => e.currentTarget.select()}
          onblur={() => setTimeout(() => { open = false; if (picked) query = picked.name; }, 150)} />
        <button class="clear" aria-label="Clear the stop" hidden={!query && !picked} onclick={() => pick(null)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
        {#if listed}
          <!-- a press on a suggestion doesn't take the focus (and the phone's keyboard) from the box -->
          <ul id="tl-stops" role="listbox" aria-label="Stops" class="list" class:up style:max-height="{room}px" onmousedown={(e) => e.preventDefault()}>
            {#each found as p, i (p.name)}
              <!-- svelte-ignore a11y_click_events_have_key_events -->
              <li id="tl-stop-{i}" role="option" tabindex="-1" aria-selected={i === active} onclick={() => choose(p)}>
                <span>{p.name}</span><small>{p.ids.length > 1 ? `${p.ids.length} stops` : ""}</small>
              </li>
            {:else}
              <li class="miss" role="option" aria-selected="false" aria-disabled="true">No stop on this map matches “{query.trim()}”</li>
            {/each}
          </ul>
        {/if}
      </div>
      {#if picked}
        <div class="day">
          <!-- a row's label above its strip: labels can be long ("#20, #56 and J14"), and the strips
               stay in the slider's column so their cursors line up with its -->
          <div class="tracks" aria-hidden="true">
            {#each rows as r}
              <div class="row">
                {#if rows.length > 1}<span class="side">{r.label}</span>{/if}
                <div class="track">
                  <svg viewBox="0 0 {data.frames} 1" preserveAspectRatio="none">
                    {#each runs(r.s) as [x, w, c]}<rect {x} width={w} height="1" class={c} />{/each}
                  </svg>
                  <i class="cursor" style:left="{((frame + 0.5) / data.frames) * 100}%"></i>
                </div>
              </div>
            {/each}
          </div>
          <div class="ticks" aria-hidden="true">
            {#each [360, 720, 1080] as m}<span style:left="{((m - data.start) / data.step / data.frames) * 100}%">{clock(m)}</span>{/each}
          </div>
          <div class="says" aria-live="polite">
            {#each told as line}<p>{line}</p>{/each}
          </div>
        </div>
      {/if}
      <div class="controls">
        <button class="play" onclick={() => (playing ? stop() : play())} aria-label={playing ? "Pause" : "Play"}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={playing ? "M6 5h4v14H6zm8 0h4v14h-4z" : "M7 5v14l12-7z"} /></svg>
        </button>
        <div class="scrub">
          <div class="readout"><strong>{time}</strong><span class="count"><b>{dark.toLocaleString()}</b> stops without their buses</span></div>
          <svg class="spark" viewBox="0 0 {data.frames - 1} 100" preserveAspectRatio="none" aria-hidden="true">
            <path class="noise" d={spark(data.noise)} />
            <path class="area" d={spark(data.dark) + `L${data.frames - 1},100L0,100Z`} />
            <line x1={frame} x2={frame} y1="0" y2="100" />
          </svg>
          <input type="range" min="0" max={data.frames - 1} value={frame} aria-label="Time of day"
            aria-valuetext="{time}: {dark.toLocaleString()} stops without their buses"
            oninput={(e) => jump(+e.currentTarget.value)} />
        </div>
      </div>
      <div class="moments" role="group" aria-label="Moments of the day">
        {#each moments as [i, what]}
          <button aria-current={frame === i || undefined} onclick={() => jump(i)}><b>{clock(data.start + i * data.step)}</b> <span>{what}</span></button>
        {/each}
      </div>
      <ul class="key">
        <li><i class="dot d"></i>None of its buses</li>
        <li><i class="dot p"></i>A route cut, other buses came</li>
        <li><i class="dot u"></i>Can't tell</li>
        <li><i class="dot s"></i>Buses as usual</li>
        <li><i class="bar ink"></i>Course</li>
        {#if data.field}<li><i class="bar amber"></i>Race</li>{/if}
      </ul>
    </div>
  {/if}
</figure>

<style>
  .timelapse { margin: 0; }
  .map { position: relative; width: 100%; aspect-ratio: 4 / 5; max-height: 78vh; border-radius: var(--radius); overflow: hidden; background: var(--surface2); }
  @media (min-width: 720px) { .map { aspect-ratio: 5 / 4; } }
  /* the page runs the map edge to edge on a phone: the rest keeps the page's gutter */
  @media (max-width: 719px) { .below { padding: 0 16px; } }

  .find { position: relative; margin-top: 10px; max-width: 460px; }
  .find input {
    appearance: none; -webkit-appearance: none; width: 100%; min-height: 44px; padding: 11px 44px 8px 38px; /* Overpass sits high: nudge */
    border: 1.5px solid var(--rule); border-radius: 10px; background: var(--surface); color: var(--asphalt);
    font: inherit; font-size: 16px; /* 16px: iOS won't zoom on focus */
  }
  .find input::placeholder { color: var(--fog); font-size: 15px; }
  .find input::-webkit-search-decoration, .find input::-webkit-search-cancel-button { -webkit-appearance: none; appearance: none; }
  .glass { position: absolute; left: 12px; top: 13.5px; width: 18px; height: 18px; fill: none; stroke: var(--fog); stroke-width: 2.4; stroke-linecap: round; pointer-events: none; }
  .clear { position: absolute; top: 0; right: 0; width: 44px; height: 44px; border: 0; background: none; color: var(--fog); cursor: pointer; display: grid; place-items: center; }
  .clear[hidden] { display: none; }
  .clear svg { width: 18px; height: 18px; stroke: currentColor; stroke-width: 2.4; stroke-linecap: round; }
  .list {
    position: absolute; z-index: 5; left: 0; right: 0; top: calc(100% + 6px); margin: 0; padding: 4px; list-style: none;
    background: var(--surface); border-radius: 12px; box-shadow: var(--shadow); overflow-y: auto;
  }
  .list.up { top: auto; bottom: calc(100% + 6px); }
  .list li { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 6px 10px 4px; border-radius: 10px; font-size: 15px; cursor: pointer; }
  .list li[aria-selected="true"], .list li:hover { background: var(--concrete); }
  .list span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .list small { margin-left: auto; padding-left: 6px; font-size: 13px; color: var(--fog); white-space: nowrap; }
  .list .miss { color: var(--fog); font-size: 14px; cursor: default; background: none !important; }

  /* the picked stop's day: in the slider's column, so its cursor lines up with the count's */
  .day { display: grid; grid-template-columns: 44px minmax(0, 1fr); column-gap: 12px; margin-top: 12px; }
  .tracks { grid-column: 2; display: grid; gap: 8px; }
  .side { display: block; margin-bottom: 3px; font-size: 13px; line-height: 1.3; color: var(--fog); }
  .track { position: relative; }
  .tracks svg { display: block; width: 100%; height: 16px; border-radius: 3px; background: var(--surface2); }
  .tracks .d { fill: var(--star-red); }
  .tracks .p { fill: var(--star-red); opacity: 0.4; }
  .tracks .u { fill: var(--fog); opacity: 0.55; }
  .tracks .s { fill: var(--flag-blue); opacity: 0.5; }
  .cursor { position: absolute; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; border-radius: 1px; background: var(--asphalt); }
  .ticks { grid-column: 2; position: relative; height: 20px; font-size: 13px; color: var(--fog); }
  .ticks span { position: absolute; top: 3px; transform: translateX(-50%); white-space: nowrap; }
  .says { grid-column: 2; font-size: 15px; line-height: 1.4; }
  .says p { margin: 2px 0 0; }

  .controls { display: flex; align-items: center; gap: 12px; margin-top: 10px; }
  .play { flex: none; width: 44px; height: 44px; border: 0; border-radius: 50%; background: var(--asphalt); color: var(--concrete); cursor: pointer; display: grid; place-items: center; }
  .play svg { width: 20px; height: 20px; fill: currentColor; }
  .scrub { flex: 1; min-width: 0; }
  .readout { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 15px; }
  .readout strong { font-size: 18px; }
  .count { color: var(--fog); }
  .count b { color: var(--star-red); }
  .spark { display: block; width: 100%; height: 34px; margin-top: 6px; overflow: visible; }
  .spark .area { fill: var(--star-red); opacity: 0.22; stroke: var(--star-red); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .spark .noise { fill: none; stroke: var(--fog); stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
  .spark line { stroke: var(--asphalt); stroke-width: 2; vector-effect: non-scaling-stroke; }
  input[type="range"] { width: 100%; margin: 0; accent-color: var(--star-red); height: 28px; }

  .moments { display: flex; gap: 6px; margin-top: 8px; }
  .moments button {
    flex: 1 1 0; min-width: 0; min-height: 44px; padding: 7.8px 2px 4.2px; /* text centred, measured: Overpass sits high */ border: 1px solid var(--rule); border-radius: 10px;
    background: var(--surface); color: var(--asphalt); font: inherit; font-size: 13px; line-height: 1.2; cursor: pointer;
  }
  .moments b { display: block; font-weight: 800; }
  .moments span { color: var(--fog); }
  .moments [aria-current] { background: var(--asphalt); border-color: var(--asphalt); color: var(--concrete); }
  .moments [aria-current] span { color: inherit; }
  @media (min-width: 720px) {
    .moments { flex-wrap: wrap; }
    .moments button { flex: none; padding: 8.8px 14px 5.2px; font-size: 14px; }
    .moments b { display: inline; margin-right: 4px; }
  }

  .key { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 10px 0 0; padding: 0; list-style: none; font-size: 13px; color: var(--fog); }
  .key li { display: flex; align-items: center; gap: 6px; }
  .key i { translate: 0 -1.7px; } /* Overpass sets text ~1.7 px high in its line: icons on the text's cap-height centre, measured in Chrome (Oct 12) */
  .dot { flex: none; width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .dot.d { background: var(--star-red); }
  .dot.s { background: var(--flag-blue); opacity: 0.6; }
  .dot.p { background: var(--star-red); opacity: 0.45; }
  .dot.u { border: 1.5px solid var(--fog); }
  .bar { width: 18px; height: 4px; border-radius: 2px; display: inline-block; }
  .bar.ink { background: var(--asphalt); }
  .bar.amber { background: var(--signal-amber); }
  .fail { color: var(--fog); }
</style>

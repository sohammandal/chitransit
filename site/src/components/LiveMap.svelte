<script lang="ts">
  // The live map: every bus from the poller's feed (live/vehicles.json, rewritten after each
  // 30 s sweep), gliding between updates. Tap a bus to see its route and the measured gap
  // behind the bus ahead. Ported from the chitransit-preview prototype (docs/SITE.md).
  import { onMount } from "svelte";
  import * as maplibregl from "maplibre-gl";
  import "maplibre-gl/dist/maplibre-gl.css";
  // MapLibre 6 parses tiles in a module worker; bundled, it can't find it on its own.
  import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

  maplibregl.setWorkerUrl(workerUrl);

  type Vehicle = {
    vid: string; rt: string; pid: number | null; hdg: number | null; lat: number | null;
    lon: number | null; dly: boolean; des: string | null; dir: string | null; t: number;
    pdist: number | null; gap_s: number | null; ahead: string | null; at_terminal: boolean;
  };
  type Feed = { generated_at: string; vehicles: Vehicle[] };
  type Pattern = { rt: string; dir: string; c: [number, number][]; s: [number, number, string, number][] };

  const FEED = "/live/vehicles.json";
  const PATTERNS = "/data/v1/patterns/";
  const SWEEP_MS = 30_000; // the poller rewrites the feed after every 30 s sweep
  const RETRY_MS = 10_000;
  const TWEEN_MS = 2_000;
  const STYLES = {
    light: "https://tiles.openfreemap.org/styles/positron",
    dark: "https://tiles.openfreemap.org/styles/dark",
  };

  let container: HTMLDivElement;
  let count = $state<number | null>(null);
  let generatedAt = $state<Date | null>(null);
  let now = $state(Date.now());
  let failed = $state(false);
  let routeFilter = $state("");
  let selected = $state<Vehicle | null>(null);
  let about = $state(false);

  const ago = $derived(generatedAt ? Math.max(0, Math.round((now - generatedAt.getTime()) / 1000)) : null);
  const selectedAge = $derived(selected ? Math.max(0, Math.round(now / 1000 - selected.t)) : null);

  function minutes(s: number): string {
    const m = Math.round(s / 60);
    return m < 1 ? "under a minute" : `${m} min`;
  }

  onMount(() => {
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    const palette = () => {
      const css = getComputedStyle(document.documentElement);
      return {
        bus: css.getPropertyValue("--flag-blue").trim(),
        late: css.getPropertyValue("--signal-amber").trim(),
        ink: css.getPropertyValue("--asphalt").trim(),
        halo: css.getPropertyValue("--surface").trim(),
      };
    };
    const map = new maplibregl.Map({
      container,
      style: dark.matches ? STYLES.dark : STYLES.light,
      center: [-87.66, 41.86],
      zoom: window.innerWidth < 640 ? 10.4 : 11.2,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(
      new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false }, trackUserLocation: false }),
      "bottom-right",
    );

    let buses = new Map<string, { from: [number, number]; to: [number, number]; v: Vehicle }>();
    let tweenStart = 0;
    let frame = 0;
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

    // A dot with a pointed nose, rotated by heading and recolored as an SDF.
    function busIcon() {
      const s = 64, c = document.createElement("canvas");
      c.width = c.height = s;
      const g = c.getContext("2d")!;
      g.fillStyle = "#000";
      g.beginPath(); g.arc(s / 2, s / 2 + 4, 20, 0, Math.PI * 2); g.fill();
      g.beginPath(); g.moveTo(s / 2, 4); g.lineTo(s / 2 + 13, s / 2 - 6); g.lineTo(s / 2 - 13, s / 2 - 6);
      g.closePath(); g.fill();
      return g.getImageData(0, 0, s, s);
    }

    function addLayers() {
      const p = palette();
      if (!map.hasImage("bus")) map.addImage("bus", busIcon(), { sdf: true, pixelRatio: 2 });
      map.addSource("sel-route", { type: "geojson", data: empty });
      map.addSource("sel-stops", { type: "geojson", data: empty });
      map.addSource("buses", { type: "geojson", data: empty });
      map.addLayer({ id: "sel-route", type: "line", source: "sel-route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": p.bus, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 3, 15, 7], "line-opacity": 0.5 } });
      map.addLayer({ id: "sel-stops", type: "circle", source: "sel-stops", minzoom: 13,
        paint: { "circle-radius": 3.5, "circle-color": p.halo, "circle-stroke-color": p.bus, "circle-stroke-width": 2 } });
      // Zoomed out: plain dots.
      map.addLayer({ id: "bus-dot", type: "circle", source: "buses", maxzoom: 13,
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 13, 5.5],
          "circle-color": ["case", ["get", "dly"], p.late, p.bus],
          "circle-stroke-color": p.halo, "circle-stroke-width": 1 } });
      // Zoomed in: heading arrows with route labels; MapLibre drops labels that would collide.
      map.addLayer({ id: "bus-sym", type: "symbol", source: "buses", minzoom: 13,
        layout: { "icon-image": "bus", "icon-size": ["interpolate", ["linear"], ["zoom"], 13, 0.55, 17, 0.9],
          "icon-rotate": ["get", "hdg"], "icon-rotation-alignment": "map", "icon-allow-overlap": true,
          "text-field": ["concat", ["get", "rt"], " ", ["get", "d"]], "text-font": ["Noto Sans Bold"],
          "text-size": 12, "text-variable-anchor": ["left", "right", "top", "bottom"], "text-radial-offset": 1.1,
          "text-optional": true, "text-padding": 3 },
        paint: { "icon-color": ["case", ["get", "dly"], p.late, p.bus], "icon-halo-color": p.halo, "icon-halo-width": 1.5,
          "text-color": p.ink, "text-halo-color": p.halo, "text-halo-width": 1.6 } });
      applyFilter();
      draw(1);
      if (selected) showRoute(selected);
    }

    function applyFilter() {
      const rt = routeFilter.trim().toUpperCase();
      if (map.getLayer("bus-sym")) map.setFilter("bus-sym", rt ? ["==", ["get", "rt"], rt] : null);
      if (map.getLayer("bus-dot"))
        map.setPaintProperty("bus-dot", "circle-opacity", rt ? ["case", ["==", ["get", "rt"], rt], 1, 0.12] : 1);
    }

    function draw(k: number) {
      const features: GeoJSON.Feature[] = [];
      for (const [, b] of buses) {
        const lon = b.from[0] + (b.to[0] - b.from[0]) * k, lat = b.from[1] + (b.to[1] - b.from[1]) * k;
        features.push({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] },
          properties: { vid: b.v.vid, rt: b.v.rt, hdg: b.v.hdg ?? 0, dly: b.v.dly, d: (b.v.dir ?? "").slice(0, 1) } });
      }
      (map.getSource("buses") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
    }
    const progress = () => Math.min(1, (performance.now() - tweenStart) / TWEEN_MS);
    function tick() {
      const k = progress();
      draw(k);
      frame = k < 1 ? requestAnimationFrame(tick) : 0;
    }

    let timer = 0;
    async function poll() {
      try {
        const r = await fetch(FEED, { cache: "no-cache" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const feed: Feed = await r.json();
        const k = progress(), next = new Map<string, { from: [number, number]; to: [number, number]; v: Vehicle }>();
        for (const v of feed.vehicles) {
          if (v.lat == null || v.lon == null) continue;
          const prev = buses.get(v.vid);
          const from: [number, number] = prev
            ? [prev.from[0] + (prev.to[0] - prev.from[0]) * k, prev.from[1] + (prev.to[1] - prev.from[1]) * k]
            : [v.lon, v.lat];
          next.set(v.vid, { from, to: [v.lon, v.lat], v });
        }
        buses = next;
        count = buses.size;
        generatedAt = new Date(feed.generated_at);
        failed = false;
        if (selected) selected = buses.get(selected.vid)?.v ?? null;
        tweenStart = performance.now();
        if (!frame) frame = requestAnimationFrame(tick);
      } catch {
        failed = true;
      }
      // Fetch again just after the next sweep lands (feed time + 30 s, plus a little for the
      // write and the 5 s edge cache), not on a blind timer; jitter spreads readers out.
      const due = generatedAt ? generatedAt.getTime() + SWEEP_MS + 2_000 - Date.now() : RETRY_MS;
      // Overdue (poller slow or down) or failed: a calm retry, never a tight loop.
      const wait = failed || due < 2_000 ? RETRY_MS : Math.min(SWEEP_MS + 5_000, due);
      timer = window.setTimeout(poll, wait + Math.random() * 1_500);
    }

    async function showRoute(v: Vehicle) {
      const setData = (id: string, data: GeoJSON.GeoJSON) =>
        (map.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData(data);
      if (v.pid == null) return;
      try {
        const pat: Pattern = await fetch(`${PATTERNS}${v.pid}.json`).then((r) => r.json());
        setData("sel-route", { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: pat.c } });
        setData("sel-stops", { type: "FeatureCollection", features: pat.s.map((s) => ({ type: "Feature",
          properties: { name: s[2] }, geometry: { type: "Point", coordinates: [s[0], s[1]] } })) });
      } catch {
        setData("sel-route", empty);
        setData("sel-stops", empty);
      }
    }
    clearSelection = () => {
      selected = null;
      for (const id of ["sel-route", "sel-stops"])
        (map.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData(empty);
    };
    setFilter = applyFilter;

    map.on("style.load", addLayers);
    for (const layer of ["bus-dot", "bus-sym"]) {
      map.on("click", layer, (e) => {
        const vid = e.features?.[0]?.properties?.vid as string | undefined;
        const b = vid ? buses.get(vid) : undefined;
        if (!b) return;
        selected = b.v;
        showRoute(b.v);
      });
      map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
    }
    poll(); // the feed doesn't wait for the basemap; buses draw once the layers exist

    const onScheme = () => map.setStyle(dark.matches ? STYLES.dark : STYLES.light);
    dark.addEventListener("change", onScheme);
    const clock = window.setInterval(() => (now = Date.now()), 1_000);
    // Save battery: stop polling while the tab is hidden, catch up when it returns.
    const onVisibility = () => {
      window.clearTimeout(timer);
      if (document.visibilityState === "visible") poll();
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      window.clearTimeout(timer);
      window.clearInterval(clock);
      dark.removeEventListener("change", onScheme);
      document.removeEventListener("visibilitychange", onVisibility);
      map.remove();
    };
  });

  let clearSelection = () => {};
  let setFilter = () => {};
</script>

<div class="live">
  <div class="map" bind:this={container}></div>

  <header class="bar">
    <a class="brand" href="/" aria-label="chitransit.fyi home">
      <!-- The mark: a route line, two buses bunched, one far ahead (a gap). -->
      <svg viewBox="0 0 44 16" aria-hidden="true"><path d="M3 8H41" /><circle cx="8" cy="8" r="4.5" /><circle cx="16" cy="8" r="4.5" /><circle cx="36" cy="8" r="4.5" /></svg>
      <span><span class="chi">chi</span>transit<b>.fyi</b></span>
    </a>
    <p class="status" aria-live="polite">
      {#if failed && count === null}
        Can't reach the live feed. Retrying.
      {:else if count === null}
        Connecting…
      {:else}
        <span class="dot" class:stale={failed}></span>
        <strong>{count}</strong> buses on the road
        <span class="muted">· {failed ? "reconnecting" : `updated ${ago} s ago`}</span>
      {/if}
    </p>
    <label class="filter">
      <span class="sr">Show one route</span>
      <input
        bind:value={routeFilter}
        oninput={() => setFilter()}
        placeholder="Route, e.g. 66"
        autocomplete="off"
        autocapitalize="characters"
        spellcheck="false"
        enterkeyhint="done"
      />
    </label>
  </header>

  {#if selected}
    <section class="sheet" aria-label={`Bus ${selected.vid}`}>
      <button class="close" onclick={clearSelection} aria-label="Close">×</button>
      <h2><span class="rt">{selected.rt}</span>{selected.dir ?? ""}</h2>
      <p>To <strong>{selected.des}</strong></p>
      <p class="gap">
        {#if selected.at_terminal}
          At the end of the line.
        {:else if selected.gap_s != null}
          The bus ahead passed here <strong>{minutes(selected.gap_s)}</strong> ago.
        {:else}
          Gap to the bus ahead: not known yet.
        {/if}
      </p>
      <p class="muted">Bus {selected.vid}{selected.dly ? " · CTA marks it delayed" : ""} · position {selectedAge} s old</p>
    </section>
  {/if}

  <footer class="foot">
    <button class="link" onclick={() => (about = !about)} aria-expanded={about}>Unofficial · about</button>
  </footer>

  {#if about}
    <section class="sheet about" aria-label="About">
      <button class="close" onclick={() => (about = false)} aria-label="Close">×</button>
      <h2>About chitransit.fyi</h2>
      <p>Every CTA bus, updated every 30 seconds. Positions can be up to about a minute old and glide between updates. The route drawn when you tap a bus is the pattern it is running now.</p>
      <p class="muted">Data provided by Chicago Transit Authority. chitransit.fyi is an independent project and is not affiliated with, made by, or endorsed by the CTA. Figures are our own estimates; data may be delayed or incomplete. Official info: <a href="https://www.transitchicago.com">transitchicago.com</a>. Map © OpenStreetMap contributors, OpenFreeMap.</p>
    </section>
  {/if}
</div>

<style>
  .live { position: fixed; inset: 0; height: 100dvh; }
  .map { position: absolute; inset: 0; }
  .bar {
    position: absolute; z-index: 2;
    top: calc(12px + var(--safe-top)); left: calc(12px + var(--safe-left));
    width: min(320px, calc(100vw - 24px - var(--safe-left) - var(--safe-right)));
    background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
    padding: 12px 14px; display: grid; gap: 8px;
  }
  .brand { display: flex; align-items: center; gap: 8px; color: var(--asphalt); text-decoration: none; font-size: 19px; font-weight: 800; letter-spacing: -0.01em; }
  .brand b { font-weight: 400; color: var(--fog); }
  .brand .chi { color: var(--flag-blue); }
  .brand svg { width: 52px; height: 19px; flex: none; }
  .brand path { stroke: var(--fog); stroke-width: 2.5; stroke-linecap: round; }
  .brand circle { fill: var(--flag-blue); stroke: var(--surface); stroke-width: 2; }
  .status { margin: 0; font-size: 15px; }
  .status strong { font-size: 17px; font-weight: 800; }
  .muted { color: var(--fog); font-size: 13px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--flag-blue); margin-right: 6px; vertical-align: 1px; animation: pulse 2s infinite; }
  .dot.stale { background: var(--signal-amber); }
  @keyframes pulse { 50% { opacity: 0.3; } }
  .filter input {
    width: 100%; font: inherit; font-size: 16px; /* 16px: iOS won't zoom on focus */
    padding: 10px 12px; min-height: 44px; border-radius: 10px;
    border: 1.5px solid var(--rule); background: var(--concrete); color: var(--asphalt);
  }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .sheet {
    position: absolute; z-index: 3; right: calc(12px + var(--safe-right)); bottom: calc(40px + var(--safe-bottom));
    width: 300px; background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
    padding: 14px 16px 16px; display: grid; gap: 6px;
  }
  .sheet h2 { margin: 0 32px 2px 0; font-size: 19px; display: flex; align-items: center; gap: 8px; }
  .sheet p { margin: 0; }
  .sheet .gap strong { color: var(--asphalt); }
  .rt { display: inline-grid; place-items: center; min-width: 44px; height: 30px; padding: 0 8px; border-radius: 8px; background: var(--asphalt); color: var(--surface); font-weight: 900; }
  .close { position: absolute; top: 4px; right: 4px; width: 44px; height: 44px; border: 0; background: none; color: var(--fog); font-size: 24px; cursor: pointer; }
  .foot { position: absolute; z-index: 2; left: calc(12px + var(--safe-left)); bottom: calc(10px + var(--safe-bottom)); }
  .link { font: inherit; font-size: 13px; color: var(--asphalt); background: var(--surface); border: 0; border-radius: 999px; padding: 8px 12px; min-height: 36px; box-shadow: var(--shadow); cursor: pointer; }
  .about { left: calc(12px + var(--safe-left)); right: auto; width: min(420px, calc(100vw - 24px)); }

  /* Phones: full-width top bar and bottom sheets, clear of the notch and home indicator. */
  @media (max-width: 640px) {
    .bar { left: calc(8px + var(--safe-left)); right: calc(8px + var(--safe-right)); width: auto; top: calc(8px + var(--safe-top)); padding: 10px 12px; }
    .sheet, .about {
      left: 0; right: 0; bottom: 0; width: auto; border-radius: 18px 18px 0 0;
      padding: 16px 18px calc(18px + var(--safe-bottom));
    }
    .foot { bottom: calc(12px + var(--safe-bottom)); }
  }
</style>

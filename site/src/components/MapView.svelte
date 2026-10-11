<script lang="ts">
  // The map: every bus, gliding between feed updates. With a route open, only that route's
  // buses show and the map fits its shape around the panel; a reroute is drawn as CTA's own map
  // draws it: the regular streets it skips dashed, their stops greyed. Tap a bus to select it.
  // Zoomed in to street level, stops show too (a route's from its own patterns, else
  // stops.json, fetched then): tap one to open it.
  import { onMount, untrack } from "svelte";
  import * as maplibregl from "maplibre-gl";
  import "maplibre-gl/dist/maplibre-gl.css";
  // MapLibre 6 parses tiles in a module worker; bundled, it can't find it on its own.
  import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
  import * as data from "../lib/data";
  import { skippedStretches, withoutSpurs } from "../lib/project";
  import { routeStops } from "../lib/stops";
  import type { Camera, Pattern, Stop, Vehicle } from "../lib/types";

  maplibregl.setWorkerUrl(workerUrl);

  let { vehicles, route, direction, selected, shapes, stopShapes, stop, padding, fit, fitNonce, focusNonce, view, onSelect, onStop, onCamera }: {
    vehicles: Vehicle[];
    route: string | null;
    direction: string | null; // a bus is open: show only its direction ("Eastbound")
    selected: string | null;
    shapes: Pattern[]; // the open route's patterns
    stopShapes: { pid: number; p: Pattern }[] | null; // show only these patterns' stops (a route, a bus); null: all
    stop: Stop | null; // the open stop, highlighted at every zoom
    padding: { top: number; bottom: number; left: number; right: number };
    fit: boolean; // frame the route (or the city) at all; false keeps the camera (a tapped bus)
    fitNonce: number; // bumped on every request to frame again
    focusNonce: number; // bumped to zoom in on the selected bus
    view: { cam: Camera | null; n: number }; // bumped to put the camera back where it was (Back)
    onSelect: (vid: string) => void;
    onStop: (stop: Stop) => void;
    onCamera: (cam: Camera) => void; // where the camera came to rest (App keeps it with the page)
  } = $props();
  const STOPS_ZOOM = 14; // stops show from here (a neighbourhood): ~6,000 would clutter the city view
  let stops = $state.raw<Stop[]>([]);
  let shown: Stop[] = []; // the stops drawn, by feature index (what a tap opens)

  const STYLES = { light: "https://tiles.openfreemap.org/styles/positron", dark: "https://tiles.openfreemap.org/styles/dark" };
  const CITY = { center: [-87.66, 41.86] as [number, number], zoom: 10.6 };
  const FRAMING = { framing: true }; // marks the app's own camera moves (moveend below)
  const TWEEN_MS = 2_000;
  const GLIDE_MIN_ZOOM = 12; // gliding only shows zoomed in; at ~20 fps to spare batteries
  const FRAME_MS = 50;
  const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

  let container: HTMLDivElement;
  let map: maplibregl.Map | undefined;
  let ready = $state(false);
  let positions = new Map<string, { from: [number, number]; to: [number, number]; v: Vehicle }>();
  let tweenStart = 0, frame = 0, lastDraw = 0;

  const color = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const source = (id: string) => map?.getSource(id) as maplibregl.GeoJSONSource | undefined;
  const progress = () => Math.min(1, (performance.now() - tweenStart) / TWEEN_MS);

  // Zoomed-in bus: a disc in flag blue with a light ring, a soft shadow and a white
  // arrow for its heading. Drawn at 3x so it stays sharp on phones; the shadow has no offset,
  // so it looks right at any rotation.
  function busIcon(fill: string, ring: string) {
    const r = 3, s = 32 * r, c = document.createElement("canvas");
    c.width = c.height = s;
    const g = c.getContext("2d")!;
    g.translate(s / 2, s / 2);
    g.scale(r, r);
    g.shadowColor = "rgb(0 0 0 / 0.35)";
    g.shadowBlur = 3 * r;
    g.beginPath(); g.arc(0, 0, 11, 0, Math.PI * 2); g.fillStyle = ring; g.fill();
    g.shadowColor = "transparent";
    g.beginPath(); g.arc(0, 0, 8.75, 0, Math.PI * 2); g.fillStyle = fill; g.fill();
    g.beginPath(); g.moveTo(0, -5.5); g.lineTo(4.5, 4.5); g.lineTo(0, 2.25); g.lineTo(-4.5, 4.5); g.closePath();
    g.fillStyle = "#fff"; g.lineJoin = "round"; g.strokeStyle = "#fff"; g.lineWidth = 1; g.fill(); g.stroke();
    return g.getImageData(0, 0, s, s);
  }
  // Our own names: the basemap sprite already has a "bus" image.
  function setImage(id: string, img: ImageData) {
    if (map!.hasImage(id)) map!.removeImage(id); // colours change with the light/dark theme
    map!.addImage(id, img, { pixelRatio: 3 });
  }

  function addLayers() {
    if (!map) return;
    const bus = color("--flag-blue"), ink = color("--asphalt"), halo = color("--surface"), fog = color("--fog");
    setImage("gl-bus", busIcon(bus, halo));
    map.addSource("route", { type: "geojson", data: empty });
    map.addSource("regular", { type: "geojson", data: empty });
    map.addSource("buses", { type: "geojson", data: empty });
    map.addSource("stops", { type: "geojson", data: stopFeatures() });
    // a stop a reroute skips: smaller and grey, as on CTA's map
    map.addLayer({ id: "stops", type: "circle", source: "stops", minzoom: STOPS_ZOOM,
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], STOPS_ZOOM, ["case", ["get", "skipped"], 3.5, 4.5], 18, ["case", ["get", "skipped"], 6, 8]],
        "circle-color": halo, "circle-stroke-color": ["case", ["get", "skipped"], fog, ink], "circle-stroke-width": ["case", ["get", "skipped"], 1.5, 2] } });
    // a finger-sized target around each small dot (invisible)
    map.addLayer({ id: "stops-hit", type: "circle", source: "stops", minzoom: STOPS_ZOOM,
      paint: { "circle-radius": 16, "circle-color": "#000", "circle-opacity": 0 } });
    // the regular path a reroute replaces, dashed under the path the buses take
    map.addLayer({ id: "regular", type: "line", source: "regular", layout: { "line-join": "round" },
      paint: { "line-color": fog, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.5, 15, 3], "line-dasharray": [2, 1.5] } });
    map.addLayer({ id: "route", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": bus, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 3, 15, 7], "line-opacity": 0.45 } });
    map.addSource("stop", { type: "geojson", data: empty });
    map.addLayer({ id: "stop", type: "circle", source: "stop",
      paint: { "circle-radius": 9, "circle-color": ink, "circle-stroke-color": halo, "circle-stroke-width": 3 } });
    map.addLayer({ id: "bus-dot", type: "circle", source: "buses", maxzoom: 13,
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 13, 5.5],
        "circle-color": bus, "circle-stroke-color": halo, "circle-stroke-width": 1 } });
    map.addLayer({ id: "bus-sym", type: "symbol", source: "buses", minzoom: 13,
      layout: { "icon-image": "gl-bus", "icon-size": ["interpolate", ["linear"], ["zoom"], 13, 0.8, 17, 1.1],
        "icon-rotate": ["get", "hdg"], "icon-rotation-alignment": "map", "icon-allow-overlap": true,
        "text-field": ["concat", ["get", "rt"], " ", ["get", "d"]], "text-font": ["Noto Sans Bold"], "text-size": 12,
        "text-variable-anchor": ["left", "right", "top", "bottom"], "text-radial-offset": 1.5, "text-optional": true, "text-padding": 3 },
      paint: { "text-color": ink, "text-halo-color": halo, "text-halo-width": 1.6 } });
    map.addLayer({ id: "selected", type: "circle", source: "buses", filter: ["==", ["get", "vid"], ""],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 10, 13, 14, 17, 19], "circle-color": "transparent", "circle-stroke-color": ink, "circle-stroke-width": 2.5 } });
    ready = true;
    draw(1);
  }

  // The open route's (or bus's) stops, from its own patterns, with the stops its reroutes skip;
  // every stop when nothing is open.
  function stopFeatures(): GeoJSON.FeatureCollection {
    const drawn = stopShapes == null ? stops.map((stop) => ({ stop, skipped: false })) : routeStops(stopShapes, stops);
    shown = drawn.map((d) => d.stop);
    return { type: "FeatureCollection", features: drawn.map(({ stop: [lon, lat, name], skipped }, i) => (
      { type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { i, name, skipped } })) };
  }
  function loadStops() {
    if (stops.length || !map || map.getZoom() < STOPS_ZOOM - 0.5) return;
    data.stops().then((s) => {
      stops = s;
    }).catch(() => {});
  }

  function draw(k: number) {
    const features: GeoJSON.Feature[] = [];
    for (const [, b] of positions) {
      const lon = b.from[0] + (b.to[0] - b.from[0]) * k, lat = b.from[1] + (b.to[1] - b.from[1]) * k;
      features.push({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] },
        properties: { vid: b.v.vid, rt: b.v.rt, hdg: b.v.hdg ?? 0, dir: b.v.dir ?? "", d: (b.v.dir ?? "").slice(0, 1) } });
    }
    source("buses")?.setData({ type: "FeatureCollection", features });
  }
  function tick(t: number) {
    const k = progress();
    if (k >= 1 || t - lastDraw >= FRAME_MS) { draw(k); lastDraw = t; }
    frame = k < 1 ? requestAnimationFrame(tick) : 0;
  }

  // New feed: glide from where each bus is drawn now to its new position.
  $effect(() => {
    const k = progress(), next = new Map<string, { from: [number, number]; to: [number, number]; v: Vehicle }>();
    for (const v of vehicles) {
      if (v.lat == null || v.lon == null) continue;
      const prev = positions.get(v.vid);
      const from: [number, number] = prev
        ? [prev.from[0] + (prev.to[0] - prev.from[0]) * k, prev.from[1] + (prev.to[1] - prev.from[1]) * k]
        : [v.lon, v.lat];
      next.set(v.vid, { from, to: [v.lon, v.lat], v });
    }
    positions = next;
    if (!ready || !map) return; // ready first: it is the reactive one
    if (map.getZoom() >= GLIDE_MIN_ZOOM) {
      tweenStart = performance.now();
      if (!frame) frame = requestAnimationFrame(tick);
    } else {
      tweenStart = 0;
      draw(1);
    }
  });

  $effect(() => {
    if (!ready || !map) return;
    source("stops")?.setData(stopFeatures());
  });

  // Route open: show only its buses; closed: everyone.
  $effect(() => {
    if (!ready || !map) return;
    const onRoute: maplibregl.FilterSpecification = ["==", ["get", "rt"], route ?? ""];
    const only: maplibregl.FilterSpecification | null = !route
      ? null
      : direction
        ? ["all", onRoute, ["==", ["get", "dir"], direction]]
        : onRoute;
    map.setFilter("bus-dot", only);
    map.setFilter("bus-sym", only);
    // One route's buses sit on its line: draw them larger so they stand out from it.
    map.setPaintProperty("bus-dot", "circle-radius", ["interpolate", ["linear"], ["zoom"], 9, route ? 4.5 : 2.5, 13, route ? 7 : 5.5]);
    map.setPaintProperty("bus-dot", "circle-stroke-width", route ? 2 : 1);
  });

  // Selected bus: ring it, and bring it into view if the panel or the edge hides it.
  $effect(() => {
    if (!ready || !map) return;
    map.setFilter("selected", ["==", ["get", "vid"], selected ?? ""]);
    const b = selected ? positions.get(selected) : undefined;
    if (b) inView(b.to);
  });
  /** Bring a point into view if the panel or the edge hides it. */
  function inView(at: [number, number]) {
    const p = map!.project(at), { clientWidth: w, clientHeight: h } = container, m = 24;
    const hidden = p.x < padding.left + m || p.x > w - padding.right - m || p.y < padding.top + m || p.y > h - padding.bottom - m;
    if (hidden) frameTo(() => map!.easeTo({ center: at, duration: 500 }, FRAMING));
  }

  // The app's own camera move in flight (a fit, a zoom to a bus, a glide back), so a padding
  // change mid-flight (the sheet or the top bar settling) re-runs it instead of cancelling it:
  // setPadding is a jump, and a Forward to a route once stayed on the last route's view (Oct 3).
  let inFlight: (() => void) | null = null;
  function frameTo(move: () => void) {
    inFlight = move;
    move();
  }

  // The panel's padding, shrunk if it would leave less than ~120 px of map (small phones):
  // with padding larger than the canvas, MapLibre skips the fit entirely.
  function fitPadding() {
    // 170 = ~120 px of map left over + the 24 px margin fitBounds adds on each side
    const room = (size: number, a: number, b: number) => Math.min(1, Math.max(0, size - 170) / Math.max(1, a + b));
    const v = room(container.clientHeight, padding.top, padding.bottom);
    const h = room(container.clientWidth, padding.left, padding.right);
    return { top: padding.top * v, bottom: padding.bottom * v, left: padding.left * h, right: padding.right * h };
  }

  // Zoom in on the selected bus when asked (a tap on a route line), never closer-out than now.
  let focused = 0;
  $effect(() => {
    if (!ready || !map || focusNonce === focused) return;
    focused = focusNonce;
    untrack(() => {
      const b = selected ? positions.get(selected) : undefined;
      if (b) frameTo(() => map!.easeTo({ center: b.to, zoom: Math.max(map!.getZoom(), 15), duration: 700 }, FRAMING));
    });
  });

  // The panel's space is the map's own padding, the single source every move below relies on.
  // (Passing padding to one animation makes MapLibre keep it, and the next fit then subtracts
  // it twice: routes opened after closing one were squeezed on phones.)
  $effect(() => {
    if (!ready || !map) return;
    const again = map.isMoving() ? inFlight : null;
    map.setPadding(fitPadding(), FRAMING);
    if (again) frameTo(again);
  });

  // The open stop, and in view if the sheet grew over it. After the padding effect above: its
  // setPadding is a jump, which would cancel this ease.
  $effect(() => {
    if (!ready || !map) return;
    source("stop")?.setData(stop
      ? { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [stop[0], stop[1]] } }
      : empty);
    if (stop) inView([stop[0], stop[1]]); // the sheet grew over it
  });

  // Draw the open route and fit the map to it: to its buses at once, again when its shapes
  // arrive; back to the whole city when closed. Not on every feed (that would fight the user).
  let fitted = "", asked = "", riderMoved = false;
  $effect(() => {
    if (!ready || !map) return;
    source("route")?.setData(shapes.length
      ? { type: "Feature", properties: {}, geometry: { type: "MultiLineString", coordinates: shapes.map((p) => withoutSpurs(p.c)) } }
      : empty);
    const regular = shapes.flatMap((p) => (p.d?.length ? skippedStretches(withoutSpurs(p.d), p.c) : []));
    source("regular")?.setData(regular.length
      ? { type: "Feature", properties: {}, geometry: { type: "MultiLineString", coordinates: regular } }
      : empty);
    const running = route != null && vehicles.some((v) => v.rt === route);
    const ask = `${fitNonce}|${route}`, key = `${ask}|${shapes.length}|${running}`;
    if (key === fitted) return;
    fitted = key;
    if (ask !== asked) [asked, riderMoved] = [ask, false]; // a new request to frame
    // re-framing the same request (its shapes arrived) never undoes the rider's own move:
    // zooming in right after opening a route was yanked back out when its shapes loaded (Oct 3)
    if (!fit || riderMoved) return;
    untrack(() => {
      if (!route) return void frameTo(() => map!.easeTo({ ...CITY, duration: 600 }, FRAMING));
      const bounds = new maplibregl.LngLatBounds();
      for (const p of shapes) for (const c of p.c) bounds.extend(c);
      for (const v of vehicles) if (v.rt === route && v.lon != null && v.lat != null) bounds.extend([v.lon, v.lat]);
      if (!bounds.isEmpty()) frameTo(() => map!.fitBounds(bounds, { padding: 24, duration: 600, maxZoom: 14 }, FRAMING));
    });
  });

  // Back to a view the rider left: exactly where it was, not re-framed. After the padding and
  // fit effects above (setPadding is a jump that would cancel a glide).
  let restored = 0;
  $effect(() => {
    if (!ready || !map || view.n === restored) return;
    restored = view.n;
    const cam = view.cam;
    // a short glide; not kept again (the page already has it), so a glide cut short by the next
    // tap never leaves a half-way view behind
    if (cam) untrack(() => frameTo(() => map!.easeTo({ center: cam.center, zoom: cam.zoom, duration: 500 }, FRAMING)));
  });

  onMount(() => {
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    map = new maplibregl.Map({
      container, style: dark.matches ? STYLES.dark : STYLES.light, ...CITY,
      attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    // Dev console and the browser tests (e2e/) read the map; nothing else does.
    const w = window as unknown as { map?: maplibregl.Map; __e2e?: boolean };
    if (import.meta.env.DEV || w.__e2e) w.map = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(
      new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false }, trackUserLocation: false }),
      "bottom-right",
    );
    map.on("style.load", () => { ready = false; addLayers(); });
    map.on("load", () => {
      // Credits fold to the (i) button (they're also in About): expanded, they span a phone.
      container.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
      container.classList.add("ready");
    });
    map.on("zoomend", loadStops);
    // Only views the rider chose (their own moves, and views put back by Back) are kept with the
    // page: the app's own framing isn't, so a page left mid-flight (a second route searched
    // before the first was framed) re-frames on Back instead of returning half-way (Oct 3)
    map.on("moveend", (e) => {
      inFlight = null; // done, or cut short by the rider (their move wins)
      if ((e as { framing?: boolean }).framing) return;
      riderMoved = true;
      onCamera({ center: map!.getCenter().toArray() as [number, number], zoom: map!.getZoom() });
    });
    map.on("click", "stops-hit", (e) => {
      // a bus under the finger wins (its own handler opens it)
      if (map!.queryRenderedFeatures(e.point, { layers: ["bus-dot", "bus-sym"] }).length) return;
      const i = e.features?.[0]?.properties?.i as number | undefined;
      if (i != null && shown[i]) onStop(shown[i]);
    });
    for (const layer of ["bus-dot", "bus-sym", "stops-hit"]) {
      map.on("click", layer, (e) => {
        const vid = e.features?.[0]?.properties?.vid as string | undefined;
        if (vid) onSelect(vid);
      });
      map.on("mouseenter", layer, () => (map!.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer, () => (map!.getCanvas().style.cursor = ""));
    }
    const onScheme = () => map?.setStyle(dark.matches ? STYLES.dark : STYLES.light);
    dark.addEventListener("change", onScheme);
    return () => {
      cancelAnimationFrame(frame);
      dark.removeEventListener("change", onScheme);
      map?.remove();
    };
  });
</script>

<div class="map" bind:this={container}></div>

<style>
  .map { position: absolute; inset: 0; }
  .map:not(:global(.ready)) :global(.maplibregl-ctrl-attrib-inner) { display: none; }
  /* Folded credits: the same 29 px square as the buttons above, on the same right edge. */
  .map :global(.maplibregl-ctrl-attrib.maplibregl-compact:not(.maplibregl-compact-show)) {
    min-width: 29px; min-height: 29px; padding: 0; margin: 0 10px 10px 0;
    border-radius: 4px; box-shadow: 0 0 0 2px rgb(0 0 0 / 0.1);
  }
  .map :global(.maplibregl-compact:not(.maplibregl-compact-show) .maplibregl-ctrl-attrib-button) {
    width: 29px; height: 29px; border-radius: 4px; background-color: transparent; background-position: center;
  }
  /* Phones: controls ride above the bottom sheet (Sheet sets --sheet); pinch replaces +/-. */
  @media (max-width: 640px) {
    .map :global(.maplibregl-ctrl-bottom-right) { bottom: var(--sheet, 30dvh); transition: bottom var(--settle, 0.32s) cubic-bezier(0.22, 1, 0.36, 1); }
    :global(html[data-sheet-drag]) .map :global(.maplibregl-ctrl-bottom-right) { transition: none; }
    .map :global(.maplibregl-ctrl-group:has(.maplibregl-ctrl-zoom-in)) { display: none; }
  }
</style>

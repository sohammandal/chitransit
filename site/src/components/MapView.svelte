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
  import { dashedStretches, withoutSpurs } from "../lib/project";
  import { routeStops } from "../lib/stops";
  import { spread } from "../lib/trains";
  import type { Camera, Lines, Mode, Pattern, Stop, Train, Vehicle } from "../lib/types";

  maplibregl.setWorkerUrl(workerUrl);

  let { vehicles, route, direction, selected, shapes, stopShapes, stop, padding, fit, fitNonce, focusNonce, view, onSelect, onStop, onCamera, onRest = () => {},
    mode = "all", trains = [], lines = null, line = null, train = null, station = null, onTrain = () => {}, onStation = () => {} }: {
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
    onRest?: (cam: Camera) => void; // where it came to rest, our own framing too (the view an × returns to)
    // The 'L' (lib/trains.ts): CTA's trains, drawn in their line's colour over the lines' tracks
    mode?: Mode; // what's shown: both, buses or trains
    trains?: Train[];
    lines?: Lines | null; // the lines as drawn (l/lines.json), once loaded
    line?: string | null; // an open line: only its trains, the others' tracks faded
    train?: string | null; // the open train ("red:812")
    station?: string | null; // the open station's map id
    onTrain?: (key: string) => void;
    onStation?: (id: string) => void;
  } = $props();
  const STATIONS_ZOOM = 11.5; // 'L' stations show from here: 143 is few, but not at city scale
  const NAMES_ZOOM = 13; // and their names from here
  const TRACK: maplibregl.ExpressionSpecification = ["interpolate", ["linear"], ["zoom"], 10, 2, 15, 5];
  // Shared track as nested stripes, the widest underneath, each wider line's edge showing either
  // side of the one above it: 1.25 px a side when zoomed out, 3 at street level (brown hid under
  // purple at 2, Oct 11; bands with gaps between broke apart where lines part, Oct 12)
  const STRIPES: maplibregl.ExpressionSpecification = ["interpolate", ["linear"], ["zoom"],
    10, ["+", 2, ["*", ["-", ["get", "w"], 1], 2.5]], 15, ["+", 5, ["*", ["-", ["get", "w"], 1], 6]]];
  const STRIPE_ORDER: maplibregl.ExpressionSpecification = ["-", 0, ["get", "w"]]; // the narrowest on top
  const BUS_LAYERS = ["route", "regular", "stops", "stops-hit", "stop", "bus-dot", "bus-sym", "selected"];
  const TRAIN_LAYERS = ["l-lines", "l-stations", "l-names", "l-stations-hit", "l-station", "train-dot", "train-arrow", "train-selected"];
  const STOPS_ZOOM = 14; // stops show from here (a neighbourhood): ~6,000 would clutter the city view
  let stops = $state.raw<Stop[]>([]);
  let shown: Stop[] = []; // the stops drawn, by feature index (what a tap opens)

  const STYLES = { light: "https://tiles.openfreemap.org/styles/positron", dark: "https://tiles.openfreemap.org/styles/dark" };
  const CITY = { center: [-87.66, 41.86] as [number, number], zoom: 10.6 };
  const FRAMING = { framing: true }; // marks the app's own camera moves (moveend below)
  const TWEEN_MS = 2_000;
  const SETTLE_MS = 320; // the sheet's own settle (Sheet.svelte --settle)
  const GLIDE_MIN_ZOOM = 12; // gliding only shows zoomed in (buses and trains); at ~20 fps to spare batteries
  const ARROW_ZOOM = 13; // a train's heading shows from here
  const FRAME_MS = 50;
  const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

  let container: HTMLDivElement;
  let map: maplibregl.Map | undefined;
  let ready = $state(false);
  type Glide<T> = { from: [number, number]; to: [number, number]; x: T };
  let positions = new Map<string, Glide<Vehicle>>();
  let trainsAt = new Map<string, Glide<Train>>();
  let tweenStart = 0, trainStart = 0, frame = 0, lastDraw = 0;
  let busGlide = false, trainGlide = false; // a glide not yet drawn to its end

  const color = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const source = (id: string) => map?.getSource(id) as maplibregl.GeoJSONSource | undefined;
  const progress = (start: number) => Math.min(1, (performance.now() - start) / TWEEN_MS);
  const along = (g: Glide<unknown>, k: number): [number, number] => [g.from[0] + (g.to[0] - g.from[0]) * k, g.from[1] + (g.to[1] - g.from[1]) * k];
  /** The new places, each gliding from where it is drawn now (k of the way through its glide). */
  function moved<T>(was: Map<string, Glide<T>>, k: number, now: [string, [number, number], T][]) {
    return new Map(now.map(([id, to, x]) => {
      const g = was.get(id);
      return [id, { from: g ? along(g, k) : to, to, x }];
    }));
  }

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
  // A train: a rounded square in its line's colour with a ring (buses are discs, so the Blue
  // Line's trains never pass for buses), a soft shadow; drawn at 3x like the bus.
  function trainIcon(fill: string, ring: string) {
    const r = 3, s = 24 * r, c = document.createElement("canvas");
    c.width = c.height = s;
    const g = c.getContext("2d")!;
    g.translate(s / 2, s / 2);
    g.scale(r, r);
    const square = (half: number, radius: number) => { g.beginPath(); g.roundRect(-half, -half, half * 2, half * 2, radius); };
    g.shadowColor = "rgb(0 0 0 / 0.35)";
    g.shadowBlur = 3 * r;
    square(9, 3.5); g.fillStyle = ring; g.fill();
    g.shadowColor = "transparent";
    square(6.75, 2.25); g.fillStyle = fill; g.fill();
    return g.getImageData(0, 0, s, s);
  }
  // Zoomed in, a train's heading: an arrow over its square (the square never turns), in the
  // colour of the line's text (dark on Yellow).
  function arrowIcon(ink: string) {
    const r = 3, s = 24 * r, c = document.createElement("canvas");
    c.width = c.height = s;
    const g = c.getContext("2d")!;
    g.translate(s / 2, s / 2);
    g.scale(r, r);
    g.beginPath(); g.moveTo(0, -4.5); g.lineTo(3.75, 3.75); g.lineTo(0, 1.9); g.lineTo(-3.75, 3.75); g.closePath();
    g.fillStyle = ink; g.lineJoin = "round"; g.strokeStyle = ink; g.lineWidth = 1; g.fill(); g.stroke();
    return g.getImageData(0, 0, s, s);
  }
  // The picked train's ring (was a circle layer: radius 14 at icon-size 1, as the circle at zoom 13)
  function ringIcon(ink: string) {
    const r = 3, s = 36 * r, c = document.createElement("canvas");
    c.width = c.height = s;
    const g = c.getContext("2d")!;
    g.translate(s / 2, s / 2);
    g.scale(r, r);
    g.beginPath(); g.arc(0, 0, 14, 0, Math.PI * 2); g.strokeStyle = ink; g.lineWidth = 2.5; g.stroke();
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
    // the 'L' tracks, under the buses' layers: track several lines share as nested stripes
    // (each piece's w, l_lines.py), the widest underneath, so every line's colour shows
    map.addSource("l-lines", { type: "geojson", data: lineFeatures() });
    map.addLayer({ id: "l-lines", type: "line", source: "l-lines", layout: { "line-cap": "round", "line-join": "round", "line-sort-key": STRIPE_ORDER },
      paint: { "line-color": ["get", "color"], "line-width": STRIPES } });
    map.addSource("route", { type: "geojson", data: empty });
    map.addSource("regular", { type: "geojson", data: empty });
    map.addSource("buses", { type: "geojson", data: empty });
    map.addSource("stops", { type: "geojson", data: stopFeatures() });
    // a stop a reroute skips: smaller and grey, as on CTA's map
    map.addLayer({ id: "stops", type: "circle", source: "stops", minzoom: STOPS_ZOOM,
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], STOPS_ZOOM, ["case", ["get", "skipped"], 3.5, 4.5], 18, ["case", ["get", "skipped"], 6, 8]],
        "circle-color": halo, "circle-stroke-color": ["case", ["get", "skipped"], fog, ink], "circle-stroke-width": ["case", ["get", "skipped"], 1.1, 1.5] } }); // thin rings, both themes (Oct 12)
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
    // 'L' stations and trains, over the buses
    map.addSource("l-stations", { type: "geojson", data: stationFeatures() });
    // larger and heavier than a bus stop, and named, so the two never pass for each other
    map.addLayer({ id: "l-stations", type: "circle", source: "l-stations", minzoom: STATIONS_ZOOM,
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], STATIONS_ZOOM, 3.5, 14, 6, 17, 9], "circle-color": halo,
        "circle-stroke-color": ink, "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], STATIONS_ZOOM, 1.5, 14, 2.25] } });
    map.addLayer({ id: "l-names", type: "symbol", source: "l-stations", minzoom: NAMES_ZOOM,
      layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Bold"], "text-size": 12,
        "text-variable-anchor": ["left", "right", "top", "bottom"], "text-radial-offset": 1.1, "text-padding": 2 },
      paint: { "text-color": ink, "text-halo-color": halo, "text-halo-width": 1.6 } });
    map.addLayer({ id: "l-stations-hit", type: "circle", source: "l-stations", minzoom: STATIONS_ZOOM,
      paint: { "circle-radius": 16, "circle-color": "#000", "circle-opacity": 0 } });
    map.addSource("l-station", { type: "geojson", data: empty });
    map.addLayer({ id: "l-station", type: "circle", source: "l-station",
      paint: { "circle-radius": 10, "circle-color": ink, "circle-stroke-color": halo, "circle-stroke-width": 3 } });
    trainImages(color("--ring"));
    map.addSource("trains", { type: "geojson", data: empty });
    const trainSize: maplibregl.ExpressionSpecification = ["interpolate", ["linear"], ["zoom"], 9, 0.5, 12, 0.8, ARROW_ZOOM, 1, 16, 1.2];
    // trains at one spot stand apart (spread): `off` in the screen's frame for the square, which
    // never turns; `aoff` in the arrow's own (turned to the heading: up is ahead)
    map.addLayer({ id: "train-dot", type: "symbol", source: "trains",
      layout: { "icon-image": ["concat", "gl-train-", ["get", "rt"]], "icon-size": trainSize, "icon-offset": ["get", "off"],
        "icon-allow-overlap": true, "icon-ignore-placement": true } });
    map.addLayer({ id: "train-arrow", type: "symbol", source: "trains", minzoom: ARROW_ZOOM, filter: ["has", "hdg"],
      layout: { "icon-image": ["concat", "gl-arrow-", ["get", "rt"]], "icon-size": trainSize, "icon-rotate": ["get", "hdg"], "icon-offset": ["get", "aoff"],
        "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true } });
    // the picked train's ring: an icon, so it moves with its train's square
    map.addLayer({ id: "train-selected", type: "symbol", source: "trains", filter: ["==", ["get", "key"], ""],
      layout: { "icon-image": "gl-train-ring", "icon-size": trainSize, "icon-offset": ["get", "off"],
        "icon-allow-overlap": true, "icon-ignore-placement": true } });
    ready = true;
    draw(1);
    drawTrains(1);
  }
  // a train's ring, white on either map (on a dark one the map's own colour hid it): its square
  // stands out from its own line, which is the same colour (rider, Oct 11)
  function trainImages(ring: string) {
    setImage("gl-train-ring", ringIcon(color("--asphalt")));
    for (const [rt, l] of Object.entries(lines?.lines ?? {})) {
      setImage(`gl-train-${rt}`, trainIcon(l.color, ring));
      setImage(`gl-arrow-${rt}`, arrowIcon(l.text));
    }
  }

  // The open route's (or bus's) stops, from its own patterns, with the stops its reroutes skip;
  // every stop when nothing is open.
  function stopFeatures(): GeoJSON.FeatureCollection {
    const drawn = stopShapes == null ? stops.map((stop) => ({ stop, skipped: false })) : routeStops(stopShapes, stops);
    shown = drawn.map((d) => d.stop);
    return { type: "FeatureCollection", features: drawn.map(({ stop: [lon, lat, name], skipped }, i) => (
      { type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { i, name, skipped } })) };
  }
  function lineFeatures(): GeoJSON.FeatureCollection {
    const features: GeoJSON.Feature[] = [];
    for (const [rt, l] of Object.entries(lines?.lines ?? {}))
      l.shapes.forEach((c, i) => features.push({ type: "Feature", geometry: { type: "LineString", coordinates: c }, properties: { rt, color: l.color, w: l.w?.[i] ?? 1 } }));
    return { type: "FeatureCollection", features };
  }
  function stationFeatures(): GeoJSON.FeatureCollection {
    return { type: "FeatureCollection", features: Object.entries(lines?.stations ?? {}).map(([id, s]) => (
      // lines as ",red,blue,": "g" (Green) mustn't match inside "org" (Orange)
      { type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: { id, name: s.name, lines: `,${s.lines.join(",")},` } })) };
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
      features.push({ type: "Feature", geometry: { type: "Point", coordinates: along(b, k) },
        properties: { vid: b.x.vid, rt: b.x.rt, hdg: b.x.hdg ?? 0, dir: b.x.dir ?? "", d: (b.x.dir ?? "").slice(0, 1) } });
    }
    source("buses")?.setData({ type: "FeatureCollection", features });
  }
  function drawTrains(k: number) {
    const features: GeoJSON.Feature[] = [];
    const at = [...trainsAt].map(([key, g]) => ({ key, g, p: along(g, k) }));
    const apart = spread(at.map(({ key, g, p }) => ({ key, lon: p[0], lat: p[1], hdg: g.x.hdg ?? undefined })));
    for (const { key, g, p } of at) {
      // [right, ahead] turned from the heading into the screen's x and y (the map never rotates)
      const [right, ahead] = apart.get(key) ?? [0, 0], h = ((g.x.hdg ?? 0) * Math.PI) / 180;
      const off = [right * Math.cos(h) + ahead * Math.sin(h), right * Math.sin(h) - ahead * Math.cos(h)];
      features.push({ type: "Feature", geometry: { type: "Point", coordinates: p },
        properties: { key, rt: g.x.rt, off, aoff: [right, -ahead], ...(g.x.hdg != null ? { hdg: g.x.hdg } : {}) } });
    }
    source("trains")?.setData({ type: "FeatureCollection", features });
  }
  function tick(t: number) {
    const kb = progress(tweenStart), kt = progress(trainStart), due = t - lastDraw >= FRAME_MS;
    if (busGlide && (due || kb >= 1)) { draw(kb); busGlide = kb < 1; }
    if (trainGlide && (due || kt >= 1)) { drawTrains(kt); trainGlide = kt < 1; }
    if (due) lastDraw = t;
    frame = busGlide || trainGlide ? requestAnimationFrame(tick) : 0;
  }

  // New feed: glide from where each bus is drawn now to its new position.
  $effect(() => {
    positions = moved(positions, progress(tweenStart),
      vehicles.flatMap((v) => (v.lat == null || v.lon == null ? [] : [[v.vid, [v.lon, v.lat], v] as [string, [number, number], Vehicle]])));
    if (!ready || !map) return; // ready first: it is the reactive one
    if (map.getZoom() >= GLIDE_MIN_ZOOM) {
      tweenStart = performance.now();
      busGlide = true;
      if (!frame) frame = requestAnimationFrame(tick);
    } else {
      tweenStart = 0;
      busGlide = false;
      draw(1);
    }
  });
  // New train positions: the same glide (CTA moves a train a track section at a time)
  $effect(() => {
    trainsAt = moved(trainsAt, progress(trainStart),
      trains.flatMap((t) => (t.lat == null || t.lon == null ? [] : [[`${t.rt}:${t.rn}`, [t.lon, t.lat], t] as [string, [number, number], Train]])));
    if (!ready || !map) return;
    if (map.getZoom() >= GLIDE_MIN_ZOOM) {
      trainStart = performance.now();
      trainGlide = true;
      if (!frame) frame = requestAnimationFrame(tick);
    } else {
      trainStart = 0;
      trainGlide = false;
      drawTrains(1);
    }
  });

  $effect(() => {
    if (!ready || !map) return;
    source("stops")?.setData(stopFeatures());
  });

  // The 'L': its tracks and stations once loaded; an open line shows only its own trains.
  $effect(() => {
    if (!ready || !map) return;
    trainImages(color("--ring"));
    source("l-lines")?.setData(lineFeatures());
    source("l-stations")?.setData(stationFeatures());
  });
  // A view opened: its own framing (if any) replaces the last one's. First of the camera effects.
  $effect(() => { void [route, line, station, stop, selected, train]; framed = null; });
  let shownStation: string | null = null;
  $effect(() => {
    if (!ready || !map) return;
    // an open line, or an open station's lines: their tracks on top of the faded others, only
    // their trains and stations (one line: plain tracks; several: their stripes)
    const s = station ? lines?.stations[station] : undefined;
    const lit = line ? [line] : s ? s.lines : null;
    const isLit: maplibregl.ExpressionSpecification = ["in", ["get", "rt"], ["literal", lit ?? []]];
    // one line lit: plain, over the others' faded stripes; several: their own stripes on top
    map.setPaintProperty("l-lines", "line-opacity", lit ? ["case", isLit, 0.9, 0.15] : 1);
    map.setPaintProperty("l-lines", "line-width", lit?.length === 1 ? TRACK : STRIPES);
    map.setLayoutProperty("l-lines", "line-sort-key", lit ? ["+", ["case", isLit, 100, 0], STRIPE_ORDER] : STRIPE_ORDER);
    const onLit: maplibregl.FilterSpecification | null = lit ? ["any", ...lit.map((rt): maplibregl.ExpressionSpecification => ["in", `,${rt},`, ["get", "lines"]])] : null;
    for (const id of ["l-stations", "l-names", "l-stations-hit"]) map.setFilter(id, onLit);
    map.setFilter("train-dot", lit ? isLit : null);
    map.setFilter("train-arrow", lit ? ["all", ["has", "hdg"], isLit] : ["has", "hdg"]);
    map.setFilter("train-selected", ["==", ["get", "key"], train ?? ""]);
    source("l-station")?.setData(s ? { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [s.lon, s.lat] } } : empty);
    // a station just opened: a move still in flight was for the view before (a zoom to the
    // train it was opened from), and would carry the map past it after this check
    if (s && station !== shownStation && inFlight) map.stop();
    shownStation = station;
    if (s) inView([s.lon, s.lat]);
  });
  // Buses, trains or both; an open route or stop shows only buses, a line, train or station only
  // trains. The other's layers hidden, not removed (a toggle back is instant)
  $effect(() => {
    if (!ready || !map) return;
    const buses = mode !== "train" && !line && !train && !station;
    const rail = mode !== "bus" && !route && !stop;
    for (const id of BUS_LAYERS) map.setLayoutProperty(id, "visibility", buses ? "visible" : "none");
    for (const id of TRAIN_LAYERS) map.setLayoutProperty(id, "visibility", rail ? "visible" : "none");
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
  // The move that framed the view shown now, if the rider hasn't moved the map since: run again
  // when the sheet settles at another height
  let framed: (() => void) | null = null;
  function frameTo(move: () => void) {
    move();
    inFlight = framed = move; // after: starting it ends the last move, whose moveend clears inFlight
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

  // Zoom in on the selected bus (a tap on a route line) or train (a tap on a station's board)
  // when asked, never closer-out than now.
  let focused = 0;
  $effect(() => {
    if (!ready || !map || focusNonce === focused) return;
    focused = focusNonce;
    untrack(() => {
      const g = train ? trainsAt.get(train) : selected ? positions.get(selected) : undefined;
      if (g) frameTo(() => map!.easeTo({ center: g.to, zoom: Math.max(map!.getZoom(), 15), duration: 700 }, FRAMING));
    });
  });

  // The panel's space is the map's own padding, the single source every move below relies on.
  // (Passing padding to one animation makes MapLibre keep it, and the next fit then subtracts
  // it twice: routes opened after closing one were squeezed on phones.) It changes at once, the
  // map staying still; then a move of ours in flight, or the framing of a view the rider hasn't
  // moved, runs again for the new space, and a view they moved glides to its middle. (A sheet
  // settling at another height made the whole map jump, Oct 11.)
  $effect(() => {
    if (!ready || !map) return;
    const pad = fitPadding(), was = map.getPadding();
    if (pad.top === was.top && pad.bottom === was.bottom && pad.left === was.left && pad.right === was.right) return;
    const moving = map.isMoving(), again = moving ? inFlight : null, center = map.getCenter();
    stillPadding(pad);
    if (moving && !again) return; // the rider's own gesture: theirs
    const redo = again ?? framed;
    if (redo) frameTo(redo);
    else map.easeTo({ center, duration: SETTLE_MS }, FRAMING);
  });
  /** New padding with nothing on screen moving: the centre becomes what's now mid-view. */
  function stillPadding(pad: maplibregl.PaddingOptions) {
    const { clientWidth: w, clientHeight: h } = container;
    const mid = map!.unproject([(pad.left + w - pad.right) / 2, (pad.top + h - pad.bottom) / 2]);
    map!.jumpTo({ center: mid, padding: pad }, FRAMING);
  }

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
    const regular = dashedStretches(shapes);
    source("regular")?.setData(regular.length
      ? { type: "Feature", properties: {}, geometry: { type: "MultiLineString", coordinates: regular } }
      : empty);
    const running = route != null && vehicles.some((v) => v.rt === route);
    const lineShapes = line ? (lines?.lines[line]?.shapes ?? []) : [];
    const ask = `${fitNonce}|${route}|${line}`, key = `${ask}|${shapes.length}|${running}|${lineShapes.length}`;
    if (key === fitted) return;
    fitted = key;
    if (ask !== asked) [asked, riderMoved] = [ask, false]; // a new request to frame
    // re-framing the same request (its shapes arrived) never undoes the rider's own move:
    // zooming in right after opening a route was yanked back out when its shapes loaded (Oct 3)
    if (!fit || riderMoved) return;
    untrack(() => {
      if (!route && line) {
        const bounds = new maplibregl.LngLatBounds();
        for (const c of lineShapes) for (const p of c) bounds.extend(p);
        if (!bounds.isEmpty()) frameTo(() => map!.fitBounds(bounds, { padding: 24, duration: 600, maxZoom: 14 }, FRAMING));
        return;
      }
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
      // at rest, once no move started in its place (a move cut short by the next isn't rest)
      queueMicrotask(() => { if (map && !map.isMoving()) onRest({ center: map.getCenter().toArray() as [number, number], zoom: map.getZoom() }); });
      if ((e as { framing?: boolean }).framing) return;
      riderMoved = true;
      framed = null;
      onCamera({ center: map!.getCenter().toArray() as [number, number], zoom: map!.getZoom() });
    });
    map.on("click", "stops-hit", (e) => {
      // a bus under the finger wins (its own handler opens it)
      if (map!.queryRenderedFeatures(e.point, { layers: ["bus-dot", "bus-sym", "train-dot", "l-stations-hit", "l-names"] }).length) return;
      const i = e.features?.[0]?.properties?.i as number | undefined;
      if (i != null && shown[i]) onStop(shown[i]);
    });
    map.on("click", "train-dot", (e) => {
      const key = e.features?.[0]?.properties?.key as string | undefined;
      if (key) onTrain(key);
    });
    map.on("click", "l-stations-hit", (e) => {
      // a train or a bus under the finger wins (their own handlers open them)
      if (map!.queryRenderedFeatures(e.point, { layers: ["train-dot", "bus-dot", "bus-sym"] }).length) return;
      const id = e.features?.[0]?.properties?.id as string | undefined;
      if (id) onStation(id);
    });
    // its name too: a train at the station covers its circle
    map.on("click", "l-names", (e) => {
      if (map!.queryRenderedFeatures(e.point, { layers: ["l-stations-hit", "train-dot", "bus-dot", "bus-sym"] }).length) return;
      const id = e.features?.[0]?.properties?.id as string | undefined;
      if (id) onStation(id);
    });
    for (const layer of ["train-dot", "l-stations-hit", "l-names"]) {
      map.on("mouseenter", layer, () => (map!.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer, () => (map!.getCanvas().style.cursor = ""));
    }
    for (const layer of ["bus-dot", "bus-sym", "stops-hit"]) {
      map.on("click", layer, (e) => {
        // a train drawn over the bus wins (its own handler opens it): one tap, one card
        if (map!.queryRenderedFeatures(e.point, { layers: ["train-dot"] }).length) return;
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

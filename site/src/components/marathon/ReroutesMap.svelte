<script lang="ts">
  // Where the buses went: CTA's own reroutes (the streets each one left, dashed red, and the ones
  // driven instead, blue), every stop it cut off (tap one: how long it went without a bus), and the
  // places where buses sat between trips (pop-up ends of the line). The route picked on the page (pickedRoute)
  // shows alone, the map fitted to it. Created only when scrolled near, so a phone never runs two
  // maps before it needs to.
  import { onMount, untrack } from "svelte";
  import * as maplibregl from "maplibre-gl";
  import "maplibre-gl/dist/maplibre-gl.css";
  import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
  import { clock, load, pickedRoute, type Course, type Reroutes } from "../../lib/marathon";

  maplibregl.setWorkerUrl(workerUrl);
  const STYLES = { light: "https://tiles.openfreemap.org/styles/positron", dark: "https://tiles.openfreemap.org/styles/dark" };

  let container: HTMLDivElement;
  let figure: HTMLElement;
  let data = $state.raw<Reroutes | null>(null);
  let failed = $state(false);
  let map: maplibregl.Map | null = null;
  let pop: maplibregl.Popup | null = null;

  function colors() {
    const css = getComputedStyle(document.documentElement);
    const v = (n: string) => css.getPropertyValue(n).trim();
    return { red: v("--star-red"), blue: v("--flag-blue"), ink: v("--asphalt"), amber: v("--signal-amber"), ground: v("--concrete") };
  }

  function addLayers(course: Course) {
    if (!map || !data) return;
    const c = colors();
    const lines: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: data.lines };
    const stops: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: data.stops.map(([rt, dir, lon, lat, name, from, back]) => ({
        type: "Feature", properties: { rt, dir, name, from, back }, geometry: { type: "Point", coordinates: [lon, lat] },
      })),
    };
    // rlist ",22,36,": a route's own waiting places, by substring (a feature's arrays reach filters as text)
    const waits: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: data.terminals.map((t) => ({
        type: "Feature", properties: { ...t, routes: t.routes.join(", "), rlist: `,${t.routes.join(",")},` }, geometry: { type: "Point", coordinates: [t.lon, t.lat] },
      })),
    };
    map.addSource("course", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: course.line } } });
    map.addSource("lines", { type: "geojson", data: lines });
    map.addSource("skipped", { type: "geojson", data: stops });
    map.addSource("waits", { type: "geojson", data: waits });
    map.addLayer({ id: "course", type: "line", source: "course", paint: { "line-color": c.ink, "line-width": 2, "line-opacity": 0.55 } });
    // a picked route's whole path, thin, under what changed: alone, #66's few changed blocks read
    // as the route (Oct 12)
    map.addLayer({ id: "route", type: "line", source: "lines", paint: { "line-color": c.blue, "line-width": 2, "line-opacity": 0.4 }, layout: { "line-join": "round", "line-cap": "round" } });
    map.addLayer({ id: "skipped-lines", type: "line", source: "lines", paint: { "line-color": c.red, "line-width": 2.5, "line-dasharray": [2, 1.5] } });
    map.addLayer({ id: "instead", type: "line", source: "lines", paint: { "line-color": c.blue, "line-width": 2.5 }, layout: { "line-join": "round" } });
    map.addLayer({ id: "skipped", type: "circle", source: "skipped",
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2, 15, 5], "circle-color": c.ground, "circle-stroke-color": c.red, "circle-stroke-width": 1.5 } });
    map.addLayer({ id: "waits", type: "circle", source: "waits",
      paint: { "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "hours"]], 2, 6, 9, 22], "circle-color": c.amber, "circle-opacity": 0.55, "circle-stroke-color": c.amber, "circle-stroke-width": 1.5 } });
    filter($pickedRoute);
  }

  // One route picked: only its lines, stops and waiting places
  function filter(rt: string) {
    if (!map?.getLayer("instead")) return;
    const kind = (k: string): maplibregl.ExpressionSpecification => ["==", ["get", "kind"], k];
    const mine: maplibregl.ExpressionSpecification = rt ? ["==", ["get", "rt"], rt] : ["literal", true];
    map.setFilter("route", rt ? ["all", kind("route"), mine] : kind("none"));
    map.setFilter("skipped-lines", ["all", kind("skipped"), mine]);
    map.setFilter("instead", ["all", kind("instead"), mine]);
    map.setFilter("skipped", rt ? mine : null);
    map.setFilter("waits", rt ? ["in", `,${rt},`, ["get", "rlist"]] : null);
  }

  // Where the map goes: the picked route's streets and stops, or every reroute
  function bounds(rt: string): maplibregl.LngLatBoundsLike {
    const b = new maplibregl.LngLatBounds();
    // to what changed: the whole route runs on off the map (#66 to Austin)
    for (const f of data!.lines) if (f.properties.rt === rt && f.properties.kind !== "route") for (const p of f.geometry.coordinates) b.extend(p);
    for (const s of data!.stops) if (s[0] === rt) b.extend([s[2], s[3]]);
    return rt && !b.isEmpty() ? b : data!.bounds;
  }
  const fit = (rt: string) => ({ padding: rt ? 28 : 12, maxZoom: 15 });

  $effect(() => {
    const rt = $pickedRoute;
    untrack(() => {
      pop?.remove();
      filter(rt);
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (map && data) map.fitBounds(bounds(rt), { ...fit(rt), duration: still ? 0 : 700 });
    });
  });

  // "66" -> "#66"; "22, 36, 151" -> "#22, #36 and #151"
  function who(routes: string): string {
    const t = routes.split(", ").map((r) => (/^\d/.test(r) ? `#${r}` : r));
    return t.length < 2 ? t.join("") : `${t.slice(0, -1).join(", ")} and ${t[t.length - 1]}`;
  }

  function popup(e: maplibregl.MapLayerMouseEvent) {
    // rings overlap where routes share a corner: the picked route's first
    const p = (e.features?.find((x) => !$pickedRoute || x.properties?.rt === $pickedRoute) ?? e.features?.[0])?.properties;
    if (!p || !map) return;
    // `from`: its first missed bus. MapLibre drops null properties, so a stop without one has no
    // `from` at all (once it read "from 12 PM": 278 of 980 rings, Oct 12)
    const span = p.from == null
      ? (p.back == null ? "all day" : `until ${clock(p.back)}`)
      : `from ${clock(p.from)} ${p.back == null ? "to the end of the day" : `to ${clock(p.back)}`}`;
    // buses parked between trips: "buses waited here" read as riders waiting (Oct 12)
    const routes = who(String(p.routes)), one = !String(p.routes).includes(",");
    const text = p.hours != null
      ? `${p.name}: ${one ? `${p.buses} ${routes} buses` : `${p.buses} buses (${routes})`} sat here between trips, ${Math.round(p.hours)} hours in all from 7 AM to 6 PM. On a normal Sunday, ${p.normal < 0.5 ? "almost none" : `about ${Math.round(p.normal)} hours`}.`
      : `${p.name}: no ${/^\d/.test(p.rt) ? "#" : ""}${p.rt} ${dirName(p.dir)} bus ${span}`;
    const el = document.createElement("p");
    el.textContent = text;
    pop?.remove();
    pop = new maplibregl.Popup({ closeButton: false, maxWidth: "240px" }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
  }
  const dirName = (d: string) => ({ N: "northbound", S: "southbound", E: "eastbound", W: "westbound" })[d] ?? "";

  onMount(() => {
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    let made = false;
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting || made) return;
      made = true;
      io.disconnect();
      Promise.all([load<Reroutes>("reroutes.json"), load<Course>("course.json")]).then(([d, course]) => {
        data = d;
        map = new maplibregl.Map({
          container, style: scheme.matches ? STYLES.dark : STYLES.light, bounds: bounds($pickedRoute), fitBoundsOptions: fit($pickedRoute),
          attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false, cooperativeGestures: true,
        });
        map.touchZoomRotate.disableRotation();
        const w = window as unknown as { reroutesMap?: maplibregl.Map; __e2e?: boolean };
        if (import.meta.env.DEV || w.__e2e) w.reroutesMap = map; // the browser tests tap its rings
        map.on("load", () => container.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show"));
        map.on("style.load", () => addLayers(course));
        for (const id of ["skipped", "waits"]) {
          map.on("click", id, popup);
          map.on("mouseenter", id, () => (map!.getCanvas().style.cursor = "pointer"));
          map.on("mouseleave", id, () => (map!.getCanvas().style.cursor = ""));
        }
        scheme.addEventListener("change", () => map?.setStyle(scheme.matches ? STYLES.dark : STYLES.light));
      }).catch(() => (failed = true));
    }, { rootMargin: "600px 0px" });
    io.observe(figure);
    return () => { io.disconnect(); map?.remove(); };
  });
</script>

<figure class="reroutes" bind:this={figure}>
  <div class="map" bind:this={container} role="img" aria-label="Map of the Marathon's bus reroutes: the regular streets each route left, dashed red, the streets driven instead in blue, the stops cut off, the places where buses sat between trips, the course in grey, and a picked route's whole path in a thin line"></div>
  {#if failed}
    <p class="fail">The map's data didn't load. Reload the page to try again</p>
  {:else}
    <!-- from the start, not once the map's data comes: it pushed the page below it down ~70 px
         as a reader scrolled past (Oct 12) -->
    <ul class="key">
      {#if $pickedRoute}<li><i class="usual"></i>Ran as usual</li>{/if}
      <li><i class="dash"></i>Regular route left</li>
      <li><i class="line"></i>Driven instead</li>
      <li><i class="ring"></i>Cut-off stop: tap one</li>
      <li><i class="blob"></i>Where buses sat between trips</li>
      <li><i class="course"></i>Marathon course</li>
    </ul>
  {/if}
</figure>

<style>
  .reroutes { margin: 0; }
  .map { position: relative; width: 100%; aspect-ratio: 4 / 5; max-height: 78vh; border-radius: var(--radius); overflow: hidden; background: var(--surface2); }
  @media (min-width: 720px) { .map { aspect-ratio: 5 / 4; } }
  .key { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 8px 0 0; padding: 0 max(0px, calc(16px - (100vw - 100%) / 2)); list-style: none; font-size: 13px; color: var(--fog); }
  .key li { display: flex; align-items: center; gap: 6px; }
  .key i { display: inline-block; width: 18px; translate: 0 -1.7px; } /* Overpass sets text ~1.7 px high in its line: icons on the text's cap-height centre, measured in Chrome (Oct 12) */
  .dash { border-top: 2.5px dashed var(--star-red); }
  .usual { height: 2px; background: var(--flag-blue); opacity: 0.4; }
  .line { height: 3px; background: var(--flag-blue); }
  .ring { width: 10px !important; height: 10px; border-radius: 50%; border: 1.5px solid var(--star-red); }
  .blob { width: 14px !important; height: 14px; border-radius: 50%; background: var(--signal-amber); opacity: 0.6; }
  .course { height: 2px; background: var(--asphalt); opacity: 0.55; } /* as its layer: ink, 2 px, 55% */
  .fail { color: var(--fog); padding: 0 max(0px, calc(16px - (100vw - 100%) / 2)); }
  .map :global(.maplibregl-popup-content) { font: 14px/1.4 var(--font); color: #1c2227; }
  .map :global(.maplibregl-popup-content p) { margin: 0; font-size: 14px; }
</style>

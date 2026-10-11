<script lang="ts">
  // The whole app: a map of every bus, and a panel with three levels.
  //   city  (/)             : "Right now", the worst long waits and buses arriving together
  //   route (/route/66/)    : Now (the route as a line per direction) and its Report card
  //   bus   (tap a bus)     : the gap ahead of and behind that bus
  //   stop  (tap a stop on the map, zoomed in): the next buses there, by stops away
  // The URL tracks the route, so a route page can be shared and Back works.
  import { onMount } from "svelte";
  import { countView } from "../lib/count";
  import * as data from "../lib/data";
  import { pollFeed, pollStopWaits } from "../lib/feed";
  import { findProblems, scheduledGap } from "../lib/problems";
  import { mainPid, onLine, onRoad } from "../lib/project";
  import { stopPatterns, stopRows } from "../lib/stops";
  import { feetBetween } from "../lib/distance";
  import { behindOf } from "../lib/waits";
  import type { Camera, Feed, Headways, Pattern, Report, Stop, StopWaits, Vehicle } from "../lib/types";
  import About from "./About.svelte";
  import BusCard from "./BusCard.svelte";
  import MapView from "./MapView.svelte";
  import RightNow from "./RightNow.svelte";
  import RouteView from "./RouteView.svelte";
  import Sheet, { HALF, PEEK } from "./Sheet.svelte";
  import StopView from "./StopView.svelte";
  import TopBar from "./TopBar.svelte";

  type Size = "peek" | "half" | "full";

  let feed = $state<Feed | null>(null);
  let waits = $state<StopWaits | null>(null); // every stop's own wait: fetched only while a stop is open
  let failed = $state(false);
  let now = $state(Date.now());
  let names = $state<Record<string, string>>({});
  let hw = $state<Headways | null>(null);
  let report = $state<Report | null>(null);
  let patterns = $state<Map<number, Pattern>>(new Map());

  let route = $state<string | null>(null);
  let selected = $state<string | null>(null);
  let stop = $state.raw<Stop | null>(null); // raw: it goes into history.state
  let fromStop = $state<string | null>(null); // a bus opened from this stop: its back link returns there
  let about = $state(false);
  let size = $state<Size>("peek");
  let phone = $state(false);
  let topH = $state(0);

  const vehicles = $derived(feed?.vehicles ?? []);
  // CTA's feed hasn't come through for a while (the poller says since when): say so, not "0 buses"
  const downSince = $derived(feed?.no_data_since
    ? new Date(feed.no_data_since).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" })
    : null);
  const at = $derived(feed ? new Date(feed.generated_at) : new Date(now));
  const age = $derived(feed ? Math.max(0, Math.round((now - at.getTime()) / 1000)) : null);
  const gapFor = (v: Vehicle) => scheduledGap(hw, v.rt, v.dir, at);
  const problems = $derived(findProblems(vehicles, gapFor));

  const routeBuses = $derived(route ? vehicles.filter((v) => v.rt === route) : []);
  const bus = $derived(selected ? vehicles.find((v) => v.vid === selected) : undefined);
  // With a bus open, the map shows only its direction: the card is all about that direction.
  const direction = $derived(bus?.dir ?? null);
  const level = $derived(about ? "about" : bus ? "bus" : route ? "route" : stop ? "stop" : "city");
  const stopLoading = $derived(stop ? stop[3].some((pid) => !patterns.has(pid)) : false);
  const rows = $derived(stop ? stopRows(stop, vehicles, patterns, waits) : []);
  const stopOpen = $derived(stop != null);
  $effect(() => {
    if (!stopOpen) return;
    const stopPolling = pollStopWaits((w) => (waits = w));
    return () => { stopPolling(); waits = null; }; // never an old wait on the next stop opened
  });
  // A stop's map: only the buses on its card, and their lines
  const stopBuses = $derived(level !== "stop" ? null : stopLoading ? [] : rows.flatMap((r) => r.next.map((n) => n.bus)));
  // The map's lines: the patterns the open route's buses (in the open bus's direction) or the open
  // stop's buses are on now
  const shapes = $derived(route ? onRoad(routeBuses.filter((b) => !direction || b.dir === direction), patterns)
    : stopBuses ? onRoad(stopBuses, patterns)
    : []);
  // The map's stops: a bus's own pattern's, a route's running patterns', none beside an open
  // stop, or all
  const stopPids = $derived(bus?.pid != null ? [bus.pid]
    : route ? [...new Set(routeBuses.flatMap((b) => (b.pid != null ? [b.pid] : [])))]
    : stopBuses ? []
    : null);
  const stopShapes = $derived(stopPids?.flatMap((pid) => (patterns.has(pid) ? [{ pid, p: patterns.get(pid)! }] : [])) ?? null);

  // Map padding keeps the route clear of the panel: beside it on wide screens, above it on phones.
  const padding = $derived(phone
    ? { top: topH + 20, bottom: Math.round(window.innerHeight * (size === "peek" ? PEEK : HALF)) + 16, left: 40, right: 40 }
    : { top: 40, bottom: 40, left: 400, right: 60 });

  // Load the open route's patterns (a route's buses can switch patterns mid-day).
  $effect(() => {
    for (const b of routeBuses) {
      const pid = b.pid;
      if (pid == null || patterns.has(pid)) continue;
      data.pattern(pid).then((p) => { patterns = new Map(patterns).set(pid, p); }).catch(() => {});
    }
  });

  // Load an open stop's patterns (where the stop is on each one): the nightly's for the stop, and
  // every pattern a bus of those routes is on (a reroute's), asked again with each feed until in
  $effect(() => {
    if (!stop) return;
    for (const pid of stopPatterns(stop, vehicles, patterns)) {
      if (patterns.has(pid)) continue;
      data.pattern(pid).then((p) => { patterns = new Map(patterns).set(pid, p); }).catch(() => {});
    }
  });
  /** Open a stop tapped on the map: its own history entry (so Back returns to it), and the
   *  camera stays where the rider put it. */
  function openStop(s: Stop) {
    go(null, "none", false);
    history.pushState({ stop: s, prev: here() }, "", "/");
    stop = s;
    size = "half";
  }

  // Phones: the map's buttons sit just above the sheet (MapView reads --sheet, which the
  // sheet sets while it's shown).
  $effect(() => {
    if (!feed && !about) document.documentElement.style.setProperty("--sheet", "0px");
  });

  // A bus that stops reporting falls back to its route.
  $effect(() => { if (selected && feed && !bus) selected = null; });

  // Map framing: asking for a route (search, a list row, the back link, Back/Forward) fits the
  // map to it; tapping a bus on the map never moves the camera (the rider picked that view), and
  // closing a route opened that way leaves the camera there too; tapping a bus on a route line
  // zooms to it (focusNonce). Each fit bumps fitNonce.
  let fit = $state(true);
  let fitNonce = $state(0);
  let focusNonce = $state(0);
  function frame(on: boolean) {
    fit = on;
    if (on) fitNonce++;
  }

  // Going back returns to the view the rider left, never a re-framed one: each page of history
  // keeps its camera (cam, updated whenever the map comes to rest) and the camera it was opened
  // from (prev, for a close that goes back without Back: a route's ×). Like scroll restoration.
  let view = $state<{ cam: Camera | null; n: number }>({ cam: null, n: 0 });
  const here = (): Camera | null => history.state?.cam ?? null;
  function keepCamera(cam: Camera) {
    history.replaceState({ ...(history.state ?? {}), cam }, "");
  }
  /** Glide back to a view; the page keeps where the glide ends (the glide itself isn't kept). */
  function restore(cam: Camera) {
    keepCamera(cam);
    view = { cam, n: view.n + 1 };
  }

  /** Open a route (or the city). history: "push" a new entry, "replace" the current one (while
   *  typing a search, so Back skips the in-between routes), or "none" (Back/Forward). */
  function go(rt: string | null, history_: "push" | "replace" | "none" = "push", fitMap = true) {
    frame(fitMap);
    stop = null;
    fromStop = null;
    route = rt;
    selected = null;
    about = false;
    size = rt ? "half" : "peek";
    const url = rt ? `/route/${encodeURIComponent(rt)}/` : "/";
    if (history_ === "push") history.pushState({ prev: here() }, "", url);
    else if (history_ === "replace") history.replaceState({ prev: history.state?.prev ?? null }, "", url);
    document.title = rt ? `${rt} ${names[rt] ?? ""} bus, live | chitransit.fyi` : "Chicago buses, live | chitransit.fyi";
    if (history_ === "push") countView(); // not while typing a search (replace)
  }
  const fromPath = () => decodeURIComponent(location.pathname.match(/^\/route\/([^/]+)/)?.[1] ?? "") || null;

  /** from "map": keep the camera; from "line": zoom the map to the bus. */
  function select(vid: string, from: "map" | "line") {
    const v = vehicles.find((x) => x.vid === vid);
    if (!v) return;
    if (from === "line" && vid === selected) return toRoute(); // tap it again: back to the route
    if (v.rt !== route) go(v.rt, "push", false);
    selected = vid;
    if (from === "line") {
      frame(false); // no route fit on top of the zoom
      focusNonce++;
    }
    about = false;
    if (size === "peek") size = "half";
  }
  /** Bus → its route: the card's back link names the route, so it shows the whole route. */
  function toRoute() {
    selected = null;
    frame(true);
  }
  /** A route's ×: back to the view the route was opened from, else the camera stays put (an ×
   *  never jumps somewhere new). */
  function close() {
    const prev: Camera | null = history.state?.prev ?? null;
    go(null, "push", false);
    if (prev) restore(prev);
  }
  function back() {
    if (about) about = false;
    else if (stop) history.back();
    else if (selected) toRoute();
    else if (route) close();
  }

  onMount(() => {
    document.querySelector(".shell")?.remove(); // index.astro's static shell, for the first paint
    route = fromPath();
    stop = history.state?.stop ?? null; // a reload keeps an open stop
    if (route || stop) size = "half";
    countView();
    const narrow = window.matchMedia("(max-width: 640px)");
    const onWidth = () => (phone = narrow.matches);
    onWidth();
    narrow.addEventListener("change", onWidth);
    const onPop = () => {
      const s: Stop | null = history.state?.stop ?? null;
      const cam = here(); // where the camera was when the rider left this page
      go(fromPath(), "none", !s && !cam);
      if (s) { stop = s; size = "half"; }
      if (cam) restore(cam);
    };
    window.addEventListener("popstate", onPop);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") back(); };
    window.addEventListener("keydown", onKey);

    data.routeNames().then((r) => (names = r)).catch(() => {});
    data.headways().then((h) => (hw = h)).catch(() => {});
    data.report().then((r) => (report = r)).catch(() => {});
    const stopPolling = pollFeed((f) => { feed = f; failed = false; }, () => (failed = true));
    const tick = window.setInterval(() => (now = Date.now()), 1000);
    return () => {
      stopPolling();
      window.clearInterval(tick);
      narrow.removeEventListener("change", onWidth);
      window.removeEventListener("popstate", onPop);
      window.removeEventListener("keydown", onKey);
    };
  });
</script>

<MapView vehicles={stopBuses ?? vehicles} {route} {direction} {selected} {shapes} {stopShapes} stop={level === "stop" ? stop : null} {padding} {fit} {fitNonce} {focusNonce} {view} onSelect={(vid) => select(vid, "map")} onStop={openStop} onCamera={keepCamera} />

<div class="side panel-column" style:--top-h="{topH}px">
  <div bind:clientHeight={topH}>
    <TopBar {route} count={feed ? vehicles.length : null} {age} {failed} {downSince} {names} onOpen={(rt, replace) => go(rt, replace ? "replace" : "push")} aboutOpen={about} onAbout={() => { about = !about; if (about && size === "peek") size = "half"; }} />
  </div>
  {#if feed || about}
    <Sheet {size} onSize={(s) => (size = s)}>
      {#if level === "about"}
        <About onClose={() => (about = false)} />
      {:else if level === "bus" && bus}
        <!-- The direction's main line, as on the route view, so a short turn stays a ring;
             a bus that isn't on it (a branch, a detour) gets its own pattern's line -->
        {@const sameDir = vehicles.filter((v) => v.rt === bus.rt && v.dir === bus.dir)}
        {@const mainP = mainPid(sameDir, patterns)}
        {@const main = mainP != null ? onLine(patterns.get(mainP)!, mainP, sameDir) : undefined}
        {@const onMain = main?.drawn.some((v) => v.vid === bus.vid) ?? false}
        {@const linePid = onMain ? mainP : bus.pid}
        {@const pattern = linePid != null ? patterns.get(linePid) : undefined}
        {@const behind = behindOf(bus, vehicles)}
        <BusCard
          {bus}
          {behind}
          behindFeet={behind ? feetBetween(bus, behind.bus, patterns) : null}
          {pattern}
          lineBuses={onMain && main ? main.drawn
            : pattern && linePid != null ? onLine(pattern, linePid, sameDir).drawn
            : vehicles.filter((v) => v.pid === bus.pid)}
          scheduled={gapFor(bus)}
          routeName={names[bus.rt]}
          age={Math.max(0, Math.round(now / 1000 - bus.t))}
          backTo={fromStop}
          onBack={fromStop ? () => history.back() : toRoute}
          onSelect={(vid) => select(vid, "line")}
        />
      {:else if level === "route" && route}
        <RouteView
          rt={route}
          name={names[route]}
          buses={routeBuses}
          {patterns}
          scheduledFor={(dir) => scheduledGap(hw, route!, dir, at)}
          days={report?.days ?? []}
          report={report?.routes[route]}
          rerouted={feed?.rerouted?.[route] ?? []}
          alerts={feed?.alerts?.[route] ?? []}
          now={new Date(now)}
          {selected}
          onSelect={(vid) => select(vid, "line")}
          onClose={close}
        />
      {:else if level === "stop" && stop}
        <StopView {stop} {rows} loading={stopLoading} {names} onSelect={(vid) => { const name = stop![2]; select(vid, "line"); fromStop = name; }} onClose={() => history.back()} />
      {:else}
        <RightNow {problems} {names} onOpen={(rt) => go(rt)} />
      {/if}
    </Sheet>
  {/if}
</div>

<style>
  /* Position: .panel-column (tokens.css). Phones: the panel is a bottom sheet (Sheet.svelte). */
  .side :global(.sheet) { flex: 0 1 auto; }
</style>

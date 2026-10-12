<script lang="ts">
  // The whole app: a map of every bus, and a panel with three levels.
  //   city  (/)             : "Right now", the worst long waits and buses arriving together
  //   route (/route/66/)    : Now (the route as a line per direction) and its Report card
  //   bus   (tap a bus)     : the gap ahead of and behind that bus
  //   stop  (tap a stop on the map, zoomed in): the next buses there, by stops away
  //   about (/about/)       : what the site is, over whatever was open
  //   line  (/line/red/)    : an 'L' line: CTA's status and its trains (CTA's data, no claims)
  //   train (tap a train)   : its next station and CTA's time; station (tap one): CTA's times
  // The URL tracks the route, the line and About, so they can be shared and Back works. The map
  // shows buses, trains or both (mode, remembered on the device).
  import { onMount } from "svelte";
  import { countView } from "../lib/count";
  import { routeUrl } from "../lib/links";
  import * as data from "../lib/data";
  import { pollFeed, pollStations, pollStopWaits, pollTrains } from "../lib/feed";
  import { LINE_NAMES, lineOf, lineUrl, trainKey } from "../lib/trains";
  import { findProblems, scheduledGap } from "../lib/problems";
  import { mainPid, onLine, onRoad } from "../lib/project";
  import { stopPatterns, stopRows } from "../lib/stops";
  import { feetBetween } from "../lib/distance";
  import { behindOf } from "../lib/waits";
  import type { Camera, Feed, Headways, Lines, Mode, Pattern, Report, StationEtas, Stop, StopWaits, TrainFeed, Vehicle } from "../lib/types";
  import About from "./About.svelte";
  import BusCard from "./BusCard.svelte";
  import MapView from "./MapView.svelte";
  import RightNow from "./RightNow.svelte";
  import RouteView from "./RouteView.svelte";
  import Sheet, { HALF, PEEK } from "./Sheet.svelte";
  import StationCard from "./StationCard.svelte";
  import StopView from "./StopView.svelte";
  import TopBar from "./TopBar.svelte";
  import TrainCard from "./TrainCard.svelte";
  import TrainLines from "./TrainLines.svelte";
  import TrainLineView from "./TrainLineView.svelte";

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
  // The 'L'
  const MODE_KEY = "mode";
  const savedMode = (): Mode => {
    try {
      const m = localStorage.getItem(MODE_KEY);
      return m === "bus" || m === "train" ? m : "all";
    } catch {
      return "all"; // storage blocked: both
    }
  };
  let mode = $state<Mode>(savedMode());
  let trainFeed = $state<TrainFeed | null>(null);
  let lines = $state.raw<Lines | null>(null);
  let line = $state<string | null>(null);
  let trainSel = $state<string | null>(null); // "red:812"
  let fromStation = $state<string | null>(null); // a train opened from this station's board: its back link returns there
  let station = $state<string | null>(null); // an open station's map id (its own history entry)
  let etas = $state<StationEtas | null>(null); // CTA's predictions: fetched only while a station is open
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
  const trains = $derived(trainFeed?.trains ?? []);
  const theTrain = $derived(trainSel ? trains.find((t) => trainKey(t) === trainSel) : undefined);
  const nowSec = $derived(Math.floor(now / 1000));
  const trainAge = $derived(trainFeed ? Math.max(0, Math.round((now - Date.parse(trainFeed.generated_at)) / 1000)) : null);
  const level = $derived(about ? "about" : theTrain ? "train" : line ? "line" : bus ? "bus" : route ? "route"
    : station ? "station" : stop ? "stop" : "city");
  // Trains are fetched while they're shown (or a line or station is open), the lines' drawing once
  const trainsShown = $derived(mode !== "bus" || line != null || station != null);
  $effect(() => {
    if (trainsShown && !lines) data.lines().then((l) => (lines = l)).catch(() => {});
  });
  $effect(() => {
    if (!trainsShown) return;
    return pollTrains((f) => (trainFeed = f), () => {});
  });
  // CTA's predictions: only while a station's board is open
  $effect(() => {
    if (!station) return;
    const stopPolling = pollStations((e) => (etas = e));
    return () => { stopPolling(); etas = null; };
  });
  // A train that stops reporting closes its card
  $effect(() => { if (trainSel && trainFeed && !theTrain) trainSel = null; });
  // The open train is kept with the page, so Back and Forward return to it (or to none)
  $effect(() => {
    const train = trainSel ?? undefined, from = fromStation ?? undefined;
    if (history.state?.train !== train || history.state?.from !== from) history.replaceState({ ...(history.state ?? {}), train, from }, "");
  });
  const trainKeys = $derived(new Set(trains.map(trainKey)));
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
    history.pushState({ stop: s, prev: opener() }, "", "/");
    stop = s;
    size = "half";
  }
  /** An 'L' station, tapped on the map or a train's next stop: like a stop, its own history entry. */
  function openStation(id: string) {
    go(null, "none", false);
    history.pushState({ station: id, prev: opener() }, "", "/");
    station = id;
    size = "half";
  }
  /** A line (search, the lines list, a train's back link): its own address, like a route. */
  function openLine(rt: string, history_: "push" | "none" = "push", fitMap = true) {
    go(null, "none", fitMap);
    line = rt;
    if (mode === "bus") setMode("all"); // a line opened is a line shown
    size = "half";
    if (history_ === "push") {
      history.pushState({ prev: opener() }, "", lineUrl(rt));
      countView();
    }
    document.title = `${LINE_NAMES[rt] ?? rt} Line trains, live | chitransit.fyi`;
  }
  /** A train tapped on the map or a line: its card; the camera stays where it is. */
  function selectTrain(key: string) {
    trainSel = trainSel === key ? null : key;
    fromStation = null;
    if (size === "peek") size = "half";
  }
  /** A train tapped on its line's strip: its card, the map zoomed to it (as a bus on its line). */
  function focusTrain(key: string) {
    selectTrain(key);
    if (trainSel) focusNonce++;
  }
  /** A time tapped on a station's board: its train over its line, the map zoomed to it; its
   *  back link (or Back) returns to the station. */
  function trainFromStation(key: string) {
    const from = station;
    openLine(key.slice(0, key.indexOf(":")), "push", false);
    trainSel = key;
    fromStation = from;
    focusNonce++;
  }
  function setMode(m: Mode) {
    mode = m;
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
      // storage blocked: remembered for this visit only
    }
    // what's open belongs to the other mode: back to the city
    if ((m === "bus" && (line || trainSel || station)) || (m === "train" && (route || stop))) go(null);
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
  let rest: Camera | null = null; // where the map last came to rest, our framing too
  // the view a new page is opened from: the rider's own, else where the map rests (the untouched
  // city an × on a line or route goes back to)
  const opener = (): Camera | null => here() ?? rest;
  function keepCamera(cam: Camera) {
    history.replaceState({ ...(history.state ?? {}), cam }, "");
  }
  /** Glide back to a view; the page keeps where the glide ends (the glide itself isn't kept). */
  function restore(cam: Camera) {
    keepCamera(cam);
    view = { cam, n: view.n + 1 };
  }

  const titleOf = (rt: string | null) => (rt ? `${rt} ${names[rt] ?? ""} bus, live | chitransit.fyi` : "Chicago buses and trains, live | chitransit.fyi");

  /** Open a route (or the city). history: "push" a new entry, "replace" the current one (while
   *  typing a search, so Back skips the in-between routes), or "none" (Back/Forward). */
  function go(rt: string | null, history_: "push" | "replace" | "none" = "push", fitMap = true) {
    frame(fitMap);
    stop = null;
    station = null;
    line = null;
    trainSel = null;
    fromStation = null;
    fromStop = null;
    route = rt;
    if (rt && mode === "train") setMode("all"); // a route opened is a route shown
    selected = null;
    about = false;
    size = rt ? "half" : "peek";
    const url = routeUrl(rt);
    if (history_ === "push") history.pushState({ prev: opener() }, "", url);
    else if (history_ === "replace") history.replaceState({ prev: history.state?.prev ?? null }, "", url);
    document.title = titleOf(rt);
    if (history_ === "push") countView(); // not while typing a search (replace)
  }
  const fromPath = () => decodeURIComponent(location.pathname.match(/^\/route\/([^/]+)/)?.[1] ?? "") || null;
  const lineFromPath = () => lineOf(decodeURIComponent(location.pathname.match(/^\/line\/([^/]+)/)?.[1] ?? ""));

  /** About has its own address (pages/about.astro). It opens over whatever is open, which stays
   *  open under it, so closing it goes Back there (or to the city, for a visit that began on it). */
  const ABOUT = "/about/";
  function showAbout() {
    about = true;
    if (size === "peek") size = "half";
    document.title = "About | chitransit.fyi";
  }
  function openAbout() {
    showAbout();
    history.pushState({ about: "over", prev: here() }, "", ABOUT);
    countView();
  }
  /** About's address gives way to the view under it: a new history entry, as a route's × makes. */
  function leaveAbout() {
    about = false;
    history.pushState(stop ? { stop, prev: here() } : { prev: here() }, "", routeUrl(route));
    document.title = titleOf(route);
  }
  function closeAbout() {
    if (history.state?.about === "over") history.back();
    else leaveAbout();
  }

  /** from "map": keep the camera; from "line": zoom the map to the bus. */
  function select(vid: string, from: "map" | "line") {
    const v = vehicles.find((x) => x.vid === vid);
    if (!v) return;
    if (from === "line" && vid === selected) return toRoute(); // tap it again: back to the route
    if (v.rt !== route) go(v.rt, "push", false);
    else if (about) leaveAbout(); // a bus of the route under About: the route's address again
    selected = vid;
    if (from === "line") {
      frame(false); // no route fit on top of the zoom
      focusNonce++;
    }
    if (size === "peek") size = "half";
  }
  /** Train → its line: the card's back link names the line, so it shows the whole line (as a bus's). */
  function toLine() {
    trainSel = null;
    frame(true);
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
    if (about) closeAbout();
    else if (fromStation) history.back();
    else if (trainSel) {
      if (line) toLine();
      else trainSel = null;
    } else if (station || stop) history.back();
    else if (line) close();
    else if (selected) toRoute();
    else if (route) close();
  }

  onMount(() => {
    document.querySelector(".shell")?.remove(); // index.astro's static shell, for the first paint
    document.querySelector(".about-copy")?.remove(); // about.astro's copy of About: the panel shows it now
    route = fromPath();
    stop = history.state?.stop ?? null; // a reload keeps an open stop
    station = history.state?.station ?? null;
    const l = lineFromPath();
    if (l) openLine(l, "none");
    if (location.pathname === ABOUT) showAbout();
    if (route || stop || station) size = "half";
    if (stop || station) frame(false); // a reload: the map brings it into view
    countView();
    const narrow = window.matchMedia("(max-width: 640px)");
    const onWidth = () => (phone = narrow.matches);
    onWidth();
    narrow.addEventListener("change", onWidth);
    const onPop = () => {
      const s: Stop | null = history.state?.stop ?? null;
      const l = lineFromPath();
      if (l) {
        const cam = here(), t: string | null = history.state?.train ?? null; // the train open on this page
        openLine(l, "none", !cam && !t);
        trainSel = t;
        fromStation = history.state?.from ?? null;
        if (cam) restore(cam);
        else if (t) focusNonce++; // the map on its train again
        return;
      }
      if (location.pathname === ABOUT) {
        showAbout(); // Forward to About, over what's open
        return;
      }
      // Back from About to the view it was opened over: still open under it, as it was
      if (about && fromPath() === route && JSON.stringify(s) === JSON.stringify(stop)) {
        about = false;
        document.title = titleOf(route);
        return;
      }
      const cam = here(); // where the camera was when the rider left this page
      const st: string | null = history.state?.station ?? null;
      go(fromPath(), "none", !s && !st && !cam);
      if (s) { stop = s; size = "half"; }
      if (st) { station = st; size = "half"; }
      else trainSel = history.state?.train ?? null; // the train open on this page
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

<MapView vehicles={stopBuses ?? vehicles} {route} {direction} {selected} {shapes} {stopShapes} stop={level === "stop" ? stop : null} {padding} {fit} {fitNonce} {focusNonce} {view} onSelect={(vid) => select(vid, "map")} onStop={openStop} onCamera={keepCamera} onRest={(cam) => (rest = cam)}
  {mode} {trains} {lines} {line} train={trainSel} station={level === "station" ? station : null} onTrain={selectTrain} onStation={openStation} />

<div class="side panel-column" style:--top-h="{topH}px">
  <div bind:clientHeight={topH}>
    <TopBar {route} count={feed ? vehicles.length : null} age={mode === "train" && trainFeed ? trainAge : age} {failed} {downSince} {names} onOpen={(rt, replace) => go(rt, replace ? "replace" : "push")} aboutOpen={about} onAbout={() => (about ? closeAbout() : openAbout())}
      {mode} onMode={setMode} trainCount={trainFeed ? trains.length : null} {lines} onOpenLine={(rt) => openLine(rt)} />
  </div>
  {#if feed || about || trainFeed}
    <Sheet {size} onSize={(s) => (size = s)} view={[level, route, selected, line, trainSel, station, stop?.[0], stop?.[1]].join("|")}>
      {#if level === "about"}
        <About onClose={closeAbout} />
      {:else if level === "train" && theTrain}
        <TrainCard train={theTrain} line={lines?.lines[theTrain.rt]} now={nowSec}
          backTo={fromStation ? (lines?.stations[fromStation]?.name ?? "Station") : null}
          onBack={() => (fromStation ? history.back() : line ? toLine() : openLine(theTrain.rt))} onStation={openStation} />
      {:else if level === "line" && line}
        <TrainLineView rt={line} line={lines?.lines[line]} {lines} {trains} loaded={trainFeed != null} status={trainFeed?.status[line]} now={nowSec}
          selected={trainSel} onSelect={focusTrain} onClose={close} />
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
      {:else if level === "station" && station}
        <StationCard id={station} {lines} etas={etas?.stations[station]?.eta ?? null} at={etas?.stations[station]?.at ?? null} loaded={etas != null}
          now={nowSec} running={trainKeys} onTrain={trainFromStation} onClose={() => history.back()} />
      {:else if level === "stop" && stop}
        <StopView {stop} {rows} loading={stopLoading} {names} onSelect={(vid) => { const name = stop![2]; select(vid, "line"); fromStop = name; }} onClose={() => history.back()} />
      {:else if mode === "train"}
        <TrainLines {lines} {trains} loaded={trainFeed != null} status={trainFeed?.status ?? {}} onOpen={(rt) => openLine(rt)} />
      {:else}
        <RightNow {problems} {names} rerouted={feed?.rerouted} onOpen={(rt) => go(rt)} />
      {/if}
    </Sheet>
  {/if}
</div>

<style>
  /* Position: .panel-column (tokens.css). Phones: the panel is a bottom sheet (Sheet.svelte). */
  .side :global(.sheet) { flex: 0 1 auto; }
</style>

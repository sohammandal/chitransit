<script lang="ts">
  // The whole app: a map of every bus, and a panel with three levels.
  //   city  (/)             : "Right now", the worst long waits and buses arriving together
  //   route (/route/66/)    : Now (the route as a line per direction) and its Report card
  //   bus   (tap a bus)     : the gap ahead of and behind that bus
  // The URL tracks the route, so a route page can be shared and Back works.
  import { onMount } from "svelte";
  import * as data from "../lib/data";
  import { pollFeed } from "../lib/feed";
  import { findProblems, scheduledGap } from "../lib/problems";
  import { onLine } from "../lib/project";
  import { behindOf } from "../lib/waits";
  import type { Feed, Headways, Pattern, Report, Vehicle } from "../lib/types";
  import About from "./About.svelte";
  import BusCard from "./BusCard.svelte";
  import MapView from "./MapView.svelte";
  import RightNow from "./RightNow.svelte";
  import RouteView from "./RouteView.svelte";
  import Sheet from "./Sheet.svelte";
  import TopBar from "./TopBar.svelte";

  type Size = "peek" | "half" | "full";

  let feed = $state<Feed | null>(null);
  let failed = $state(false);
  let now = $state(Date.now());
  let names = $state<Record<string, string>>({});
  let hw = $state<Headways | null>(null);
  let report = $state<Report | null>(null);
  let patterns = $state<Map<number, Pattern>>(new Map());

  let route = $state<string | null>(null);
  let selected = $state<string | null>(null);
  let about = $state(false);
  let size = $state<Size>("peek");
  let phone = $state(false);
  let topH = $state(0);

  const vehicles = $derived(feed?.vehicles ?? []);
  const at = $derived(feed ? new Date(feed.generated_at) : new Date(now));
  const age = $derived(feed ? Math.max(0, Math.round((now - at.getTime()) / 1000)) : null);
  const gapFor = (v: Vehicle) => scheduledGap(hw, v.rt, v.dir, at);
  const problems = $derived(findProblems(vehicles, gapFor));

  const routeBuses = $derived(route ? vehicles.filter((v) => v.rt === route) : []);
  const bus = $derived(selected ? vehicles.find((v) => v.vid === selected) : undefined);
  // With a bus open, the map shows only its direction: the card is all about that direction.
  const direction = $derived(bus?.dir ?? null);
  const shapes = $derived(route ? [...patterns.values()].filter((p) => p.rt === route && (!direction || p.dir === direction)) : []);
  const level = $derived(about ? "about" : bus ? "bus" : route ? "route" : "city");

  // Map padding keeps the route clear of the panel: beside it on wide screens, above it on phones.
  const padding = $derived(phone
    ? { top: topH + 20, bottom: Math.round(window.innerHeight * (size === "peek" ? 0.38 : 0.56)) + 16, left: 40, right: 40 }
    : { top: 40, bottom: 40, left: 400, right: 60 });

  // Load the open route's patterns (a route's buses can switch patterns mid-day).
  $effect(() => {
    for (const b of routeBuses) {
      const pid = b.pid;
      if (pid == null || patterns.has(pid)) continue;
      data.pattern(pid).then((p) => { patterns = new Map(patterns).set(pid, p); }).catch(() => {});
    }
  });

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

  /** Open a route (or the city). history: "push" a new entry, "replace" the current one (while
   *  typing a search, so Back skips the in-between routes), or "none" (Back/Forward). */
  function go(rt: string | null, history_: "push" | "replace" | "none" = "push", fitMap = true) {
    frame(fitMap);
    route = rt;
    selected = null;
    about = false;
    size = rt ? "half" : "peek";
    const url = rt ? `/route/${encodeURIComponent(rt)}/` : "/";
    if (history_ === "push") history.pushState(null, "", url);
    else if (history_ === "replace") history.replaceState(null, "", url);
    document.title = rt ? `${rt} ${names[rt] ?? ""} bus, live | chitransit.fyi` : "Chicago buses, live | chitransit.fyi";
  }
  const fromPath = () => decodeURIComponent(location.pathname.match(/^\/route\/([^/]+)/)?.[1] ?? "") || null;

  /** from "map": keep the camera; from "line": zoom the map to the bus. */
  function select(vid: string, from: "map" | "line") {
    const v = vehicles.find((x) => x.vid === vid);
    if (!v) return;
    if (v.rt !== route) go(v.rt, "push", false);
    selected = vid;
    if (from === "line") {
      frame(false); // no route fit on top of the zoom
      focusNonce++;
    }
    about = false;
    if (size === "peek") size = "half";
  }
  /** Bus → its route (fits the route), route → city (the city view only if we'd framed the route). */
  function toRoute() {
    selected = null;
    frame(true);
  }
  const close = () => go(null, "push", fit);
  function back() {
    if (about) about = false;
    else if (selected) toRoute();
    else if (route) close();
  }

  onMount(() => {
    document.querySelector(".shell")?.remove(); // index.astro's static shell, for the first paint
    route = fromPath();
    if (route) size = "half";
    const narrow = window.matchMedia("(max-width: 640px)");
    const onWidth = () => (phone = narrow.matches);
    onWidth();
    narrow.addEventListener("change", onWidth);
    const onPop = () => go(fromPath(), "none");
    window.addEventListener("popstate", onPop);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") back(); };
    window.addEventListener("keydown", onKey);

    data.routeNames().then((r) => (names = r)).catch(() => {});
    data.headways().then((h) => (hw = h)).catch(() => {});
    data.report().then((r) => (report = r)).catch(() => {});
    const stop = pollFeed((f) => { feed = f; failed = false; }, () => (failed = true));
    const tick = window.setInterval(() => (now = Date.now()), 1000);
    return () => {
      stop();
      window.clearInterval(tick);
      narrow.removeEventListener("change", onWidth);
      window.removeEventListener("popstate", onPop);
      window.removeEventListener("keydown", onKey);
    };
  });
</script>

<MapView {vehicles} {route} {direction} {selected} {shapes} {padding} {fit} {fitNonce} {focusNonce} onSelect={(vid) => select(vid, "map")} />

<div class="side panel-column" style:--top-h="{topH}px">
  <div bind:clientHeight={topH}>
    <TopBar count={feed ? vehicles.length : null} {age} {failed} {names} onOpen={(rt, replace) => go(rt, replace ? "replace" : "push")} aboutOpen={about} onAbout={() => { about = !about; if (about && size === "peek") size = "half"; }} />
  </div>
  {#if feed || about}
    <Sheet {size} onSize={(s) => (size = s)}>
      {#if level === "about"}
        <About onClose={() => (about = false)} />
      {:else if level === "bus" && bus}
        {@const pattern = bus.pid != null ? patterns.get(bus.pid) : undefined}
        <BusCard
          {bus}
          behind={behindOf(bus, vehicles)}
          {pattern}
          lineBuses={pattern && bus.pid != null
            ? onLine(pattern, bus.pid, vehicles.filter((v) => v.rt === bus.rt && v.dir === bus.dir)).drawn
            : vehicles.filter((v) => v.pid === bus.pid)}
          scheduled={gapFor(bus)}
          routeName={names[bus.rt]}
          age={Math.max(0, Math.round(now / 1000 - bus.t))}
          onBack={toRoute}
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
          now={new Date(now)}
          {selected}
          onSelect={(vid) => select(vid, "line")}
          onClose={close}
        />
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

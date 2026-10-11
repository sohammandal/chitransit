// A stop's next buses: for each route and direction that stops here, the next two buses by
// where they are now (stops away), never a forecast. Plus what's measured here: how long since
// the last bus passed this stop (its own stamp, live/stops.json: never another stop's wait,
// which a bus heading here carries for its next stop), and whether the next two are together.
import type { Pattern, Stop, StopWaits, Vehicle } from "./types";

const SAME_STOP_M = 150; // a pattern's stop of this name this close is this stop
/** Both spellings of a corner ("Ashland & Chicago", "Chicago & Ashland"), as stops.json groups them */
const corner = (name: string) => name.split(" & ").sort().join(" & ");

export type StopBus = { bus: Vehicle; away: number; feet: number }; // away: stops to go (0: at the stop); feet along the route
/** Since the last bus passed this stop: s seconds (null: can't tell); long: a long wait here */
export type StopWait = { s: number | null; long: boolean };
export type StopRow = {
  rt: string; dir: string; next: StopBus[]; wait: StopWait | null; together: boolean;
  skipped: boolean; // CTA's reroute of this route and direction skips this stop (its pattern says so)
  unknown: boolean; // a bus of this route and direction is on a pattern not loaded (yet): it may be coming
};

/** Where `stop` is on pattern `p`: the index of its entry in p.s, or null (not on it, or its
 *  last stop). */
export function stopOnPattern(stop: Stop, p: Pattern): number | null {
  const [lon, lat] = stop, name = corner(stop[2]);
  let best: number | null = null, bestM = SAME_STOP_M;
  p.s.forEach(([x, y, n], i) => {
    if (corner(n) !== name || i === p.s.length - 1) return; // nobody boards at a pattern's last stop
    const m = Math.hypot((x - lon) * 82_990, (y - lat) * 110_540);
    if (m < bestM) [best, bestM] = [i, m];
  });
  return best;
}

/** Whether a reroute's pattern skips `stop` (it lists it among the regular stops it skips). */
const skips = (stop: Stop, p: Pattern) =>
  (p.x ?? []).some(([x, y, n]) => corner(n) === corner(stop[2]) && Math.hypot((x - stop[0]) * 82_990, (y - stop[1]) * 110_540) < SAME_STOP_M);

/** The patterns to look at for a stop: those the nightly lists for it, and every pattern a bus of
 *  their routes is on now (a reroute's is new to the nightly: Oct 10, #6 and #146). */
export function stopPatterns(stop: Stop, vehicles: Vehicle[], patterns: Map<number, Pattern>): number[] {
  const routes = new Set(stop[3].flatMap((pid) => patterns.get(pid)?.rt ?? []));
  const buses = vehicles.flatMap((v) => (v.pid != null && routes.has(v.rt) ? [v.pid] : []));
  return [...new Set([...stop[3], ...buses])];
}

export function stopRows(stop: Stop, vehicles: Vehicle[], patterns: Map<number, Pattern>, waits: StopWaits | null = null): StopRow[] {
  const lines = new Map<string, StopBus[]>();
  const here = new Map<string, StopWait>();
  const skipped = new Set<string>(), unknown = new Set<string>();
  const routes = new Set(stop[3].flatMap((pid) => patterns.get(pid)?.rt ?? []));
  const running = new Set(vehicles.map((v) => v.pid));
  for (const pid of stopPatterns(stop, vehicles, patterns)) {
    const p = patterns.get(pid);
    if (!p) continue;
    const key = `${p.rt}|${p.dir}`;
    if (running.has(pid) && skips(stop, p)) skipped.add(key);
    const i = stopOnPattern(stop, p);
    if (i == null) continue;
    const at = p.s[i][3];
    if (!lines.has(key)) lines.set(key, []);
    const w = waits?.stops[key]?.[p.s[i][5] ?? ""];
    if (w && !here.has(key)) here.set(key, { s: w[0], long: w[1] === 1 });
    for (const v of vehicles) {
      if (v.pid !== pid || v.pdist == null || v.pdist > at) continue; // another pattern, or past it
      if (v.next_stop === null && v.trip !== "starting") continue; // off its route, or finishing
      const away = p.s.filter(([, , , feet]) => feet > v.pdist! && feet <= at).length;
      lines.get(key)!.push({ bus: v, away, feet: at - v.pdist });
    }
  }
  // A bus of a route here on a pattern we don't have: it may be heading here, so no "none coming"
  for (const v of vehicles) if (v.pid != null && v.dir && routes.has(v.rt) && !patterns.has(v.pid)) unknown.add(`${v.rt}|${v.dir}`);
  for (const key of skipped) if (!lines.has(key)) lines.set(key, []);
  return [...lines.entries()]
    .map(([key, buses]) => {
      const [rt, dir] = key.split("|");
      const next = buses.sort((a, b) => a.away - b.away || (b.bus.pdist ?? 0) - (a.bus.pdist ?? 0)).slice(0, 2);
      const [a, b] = next;
      return {
        rt, dir, next,
        wait: here.get(key) ?? null,
        together: !!a && !!b && (b.bus.together_with === a.bus.vid || a.bus.together_with === b.bus.vid),
        skipped: skipped.has(key) && !next.length,
        unknown: unknown.has(key) && !next.length,
      };
    })
    .sort((x, y) => x.rt.localeCompare(y.rt, undefined, { numeric: true }) || x.dir.localeCompare(y.dir));
}

/** A route's (or a bus's) stops on the map, from its patterns (the live ones: a reroute's own
 *  stops too), merged by corner as stops.json merges them and given the nightly's patterns at
 *  that corner as well (other routes stop there too); then the regular stops its reroutes skip,
 *  marked skipped (a corner some pattern still serves isn't). */
export function routeStops(shapes: { pid: number; p: Pattern }[], index: Stop[]): { stop: Stop; skipped: boolean }[] {
  const near = (a: Stop, lon: number, lat: number) => Math.hypot((a[0] - lon) * 82_990, (a[1] - lat) * 110_540) < SAME_STOP_M;
  const known = new Map<string, Stop[]>();
  for (const s of index) known.set(corner(s[2]), [...(known.get(corner(s[2])) ?? []), s]);
  const out: { stop: Stop; skipped: boolean }[] = [];
  const add = (lon: number, lat: number, name: string, pid: number | null, skipped: boolean) => {
    const hit = out.find((o) => corner(o.stop[2]) === corner(name) && near(o.stop, lon, lat));
    if (hit) {
      if (pid != null && !hit.stop[3].includes(pid)) hit.stop[3].push(pid);
      hit.skipped &&= skipped;
      return;
    }
    const same = (known.get(corner(name)) ?? []).find((s) => near(s, lon, lat));
    const pids = [...(same?.[3] ?? [])];
    if (pid != null && !pids.includes(pid)) pids.push(pid);
    out.push({ stop: [same?.[0] ?? lon, same?.[1] ?? lat, same?.[2] ?? name, pids], skipped });
  };
  for (const { pid, p } of shapes) p.s.forEach(([lon, lat, name], i) => { if (i < p.s.length - 1) add(lon, lat, name, pid, false); });
  for (const { p } of shapes) for (const [lon, lat, name] of p.x ?? []) add(lon, lat, name, null, true);
  return out;
}

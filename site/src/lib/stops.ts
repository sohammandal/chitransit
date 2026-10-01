// A stop's next buses: for each route and direction that stops here, the next two buses by
// where they are now (stops away), never a forecast. Plus what's measured here: how long since
// the last bus passed this stop (its own stamp, live/stops.json: never another stop's wait,
// which a bus heading here carries for its next stop), and whether the next two are together.
import type { Pattern, Stop, StopWaits, Vehicle } from "./types";

const SAME_STOP_M = 150; // a pattern's stop of this name this close is this stop
/** Both spellings of a corner ("Ashland & Chicago", "Chicago & Ashland"), as stops.json groups them */
const corner = (name: string) => name.split(" & ").sort().join(" & ");

export type StopBus = { bus: Vehicle; away: number }; // away: stops to go (0: at the stop)
/** Since the last bus passed this stop: s seconds (null: can't tell); long: a long wait here */
export type StopWait = { s: number | null; long: boolean };
export type StopRow = { rt: string; dir: string; next: StopBus[]; wait: StopWait | null; together: boolean };

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

export function stopRows(stop: Stop, vehicles: Vehicle[], patterns: Map<number, Pattern>, waits: StopWaits | null = null): StopRow[] {
  const lines = new Map<string, StopBus[]>();
  const here = new Map<string, StopWait>();
  for (const pid of stop[3]) {
    const p = patterns.get(pid);
    const i = p ? stopOnPattern(stop, p) : null;
    if (!p || i == null) continue;
    const at = p.s[i][3], key = `${p.rt}|${p.dir}`;
    if (!lines.has(key)) lines.set(key, []);
    const w = waits?.stops[key]?.[p.s[i][5] ?? ""];
    if (w && !here.has(key)) here.set(key, { s: w[0], long: w[1] === 1 });
    for (const v of vehicles) {
      if (v.pid !== pid || v.pdist == null || v.pdist > at) continue; // another pattern, or past it
      if (v.next_stop === null && v.trip !== "starting") continue; // off its route, or finishing
      const away = p.s.filter(([, , , feet]) => feet > v.pdist! && feet <= at).length;
      lines.get(key)!.push({ bus: v, away });
    }
  }
  return [...lines.entries()]
    .map(([key, buses]) => {
      const [rt, dir] = key.split("|");
      const next = buses.sort((a, b) => a.away - b.away || (b.bus.pdist ?? 0) - (a.bus.pdist ?? 0)).slice(0, 2);
      const [a, b] = next;
      return {
        rt, dir, next,
        wait: here.get(key) ?? null,
        together: !!a && !!b && (b.bus.together_with === a.bus.vid || a.bus.together_with === b.bus.vid),
      };
    })
    .sort((x, y) => x.rt.localeCompare(y.rt, undefined, { numeric: true }) || x.dir.localeCompare(y.dir));
}

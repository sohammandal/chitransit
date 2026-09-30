// A stop's next buses: for each route and direction that stops here, the next two buses by
// where they are now (stops away), never a forecast. Plus two measured flags from the feed:
// the next bus is in a long gap (long_wait at its next stop), and the next two are together.
import type { Pattern, Stop, Vehicle } from "./types";

const SAME_STOP_M = 150; // a pattern's stop of this name this close is this stop
/** Both spellings of a corner ("Ashland & Chicago", "Chicago & Ashland"), as stops.json groups them */
const corner = (name: string) => name.split(" & ").sort().join(" & ");

export type StopBus = { bus: Vehicle; away: number }; // away: stops to go (0: at the stop)
export type StopRow = { rt: string; dir: string; next: StopBus[]; longGap: boolean; together: boolean };

/** Where `stop` is on pattern `p`: its feet along it, or null (not on it, or its last stop). */
export function feetOnPattern(stop: Stop, p: Pattern): number | null {
  const [lon, lat] = stop, name = corner(stop[2]);
  let best: number | null = null, bestM = SAME_STOP_M;
  p.s.forEach(([x, y, n, feet], i) => {
    if (corner(n) !== name || i === p.s.length - 1) return; // nobody boards at a pattern's last stop
    const m = Math.hypot((x - lon) * 82_990, (y - lat) * 110_540);
    if (m < bestM) [best, bestM] = [feet, m];
  });
  return best;
}

export function stopRows(stop: Stop, vehicles: Vehicle[], patterns: Map<number, Pattern>): StopRow[] {
  const lines = new Map<string, StopBus[]>();
  for (const pid of stop[3]) {
    const p = patterns.get(pid);
    const at = p ? feetOnPattern(stop, p) : null;
    if (!p || at == null) continue;
    const key = `${p.rt}|${p.dir}`;
    if (!lines.has(key)) lines.set(key, []);
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
        longGap: a?.bus.long_wait === true,
        together: !!a && !!b && (b.bus.together_with === a.bus.vid || a.bus.together_with === b.bus.vid),
      };
    })
    .sort((x, y) => x.rt.localeCompare(y.rt, undefined, { numeric: true }) || x.dir.localeCompare(y.dir));
}

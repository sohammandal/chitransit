// One definition of "long wait" and "arriving together" for every view (the line, Right now,
// the bus card). The live feed measures each bus's wait at its next stop, counting every bus
// of the route and direction (short turns and branches too), and carries the schedule there
// (ghost_ledger.live). Replaying Sep 27-28, every long wait this flags was real but for
// tracker lag (a bus had passed, its report not yet in).
//   long wait : over twice the stop's scheduled gap, with at least two scheduled buses due
//               since the last one came: buses were due and didn't come. (The first bus after
//               a midday or overnight break has waited hours with nothing due: not a wait.)
//   together  : the bus ahead on this pattern passed the spot where this bus is now under a
//               quarter of the scheduled gap ago (togetherWith: measured where the bus is)
// A feed without those fields (an older poller) falls back to the same-pattern gap and the
// route's hourly schedule.
import type { Vehicle } from "./types";

export type Wait = {
  s: number | null; // seconds since the last bus passed (null: can't tell)
  by: string | null; // the bus that did
  at: string | null; // where: the stop the wait is measured at
  sched: number | null; // scheduled seconds between buses there (null: no judgment)
  due: number | null; // scheduled buses due there since the last one (null: not known)
};

const FALLBACK_TOGETHER_S = 120; // when nothing says how often buses should come

export function waitOf(v: Vehicle, routeGap: number | null): Wait {
  if (v.stop_gap_s === undefined) return { s: v.gap_s, by: v.ahead, at: v.near ?? null, sched: routeGap, due: null };
  // Schedule fields both empty: the poller has no schedule (use the route's); sched_s empty with
  // sched_due known: nothing was scheduled there this hour, so no judgment.
  const sched = v.sched_s ?? (v.sched_due == null ? routeGap : null);
  return { s: v.stop_gap_s, by: v.last_by ?? null, at: v.next_stop ?? null, sched, due: v.sched_due ?? null };
}

export const isLongWait = (w: Wait): boolean =>
  w.s != null && w.sched != null && w.s > 2 * w.sched && (w.due == null || w.due >= 2);

/** The bus this one is together with, or null. Judged at the bus's own position (gap_s: how
 *  long ago the bus ahead on its pattern passed the spot where it is now), not at its next
 *  stop: time since the last bus passed the next stop is only a lower bound until this bus
 *  gets there, fine for "long wait" (the gap is at least that) but not for "together" (#146,
 *  Sep 28: buses 2-3 miles apart on Lake Shore Drive were "5 s" apart at Belmont, the next
 *  stop). Another pattern's bus can only have passed sooner, so this never claims too much;
 *  a short turn bunched with a full-route bus goes unflagged (can't tell). */
export function togetherWith(v: Vehicle, routeGap: number | null): string | null {
  if (v.gap_s == null || !v.ahead || v.at_terminal) return null;
  const sched = waitOf(v, routeGap).sched;
  return (sched ? v.gap_s < 0.25 * sched : v.gap_s <= FALLBACK_TOGETHER_S) ? v.ahead : null;
}

/** Two buses of a route heading for the same next stop measure the same hole there: the
 *  wait is one, and belongs to the bus that gets there first. */
export const sameNextStop = (a: Vehicle, b: Vehicle): boolean =>
  a.next_stpid != null && a.next_stpid === b.next_stpid && a.rt === b.rt && a.dir === b.dir;

const BEHIND_MARGIN_M = 800; // GPS and a route's jogs off its compass direction

/** Metres along a bus's direction of travel (larger = further along), from its GPS position:
 *  CTA directions are compass names. Null when it can't say. */
function along(v: Vehicle, dir: string | null): number | null {
  if (v.lat == null || v.lon == null) return null;
  const x = v.lon * 82_990, y = v.lat * 110_540; // metres per degree at Chicago's latitude
  return { Northbound: y, Southbound: -y, Eastbound: x, Westbound: -x }[dir ?? ""] ?? null;
}

/** Is `by`, the last bus to pass `v`'s next stop, still ahead of `v`? If it's behind now, that
 *  pass was on an earlier trip and it has since turned around: it isn't the bus in front of
 *  `v`, and `v` isn't behind it (#95 Sep 28: a bus bunched at the east end was taken for the
 *  bus in front of one near the west end, 7 miles ahead of it). Heading the other way now: no.
 *  Same pattern: feet along it;
 *  otherwise GPS along the direction, clearly behind only by more than BEHIND_MARGIN_M. */
export function stillAhead(v: Vehicle, by: Vehicle): boolean {
  if (v.dir && by.dir && v.dir !== by.dir) return false; // it has since turned around
  if (v.pid != null && v.pid === by.pid && v.pdist != null && by.pdist != null) return by.pdist >= v.pdist;
  const a = along(v, v.dir), b = along(by, v.dir); // along v's direction: `by` may have turned
  return a == null || b == null || b > a - BEHIND_MARGIN_M;
}

/** The bus behind `bus`: the nearest one whose last bus (at its next stop) was `bus`. */
export function behindOf(bus: Vehicle, vehicles: Vehicle[]): Vehicle | undefined {
  if (bus.stop_gap_s === undefined) return vehicles.find((v) => v.ahead === bus.vid && v.pid === bus.pid);
  return vehicles
    .filter((v) => v.last_by === bus.vid && v.stop_gap_s != null && !v.at_terminal && stillAhead(v, bus))
    .sort((a, b) => a.stop_gap_s! - b.stop_gap_s!)[0];
}

/** The scheduled gap a direction's header shows: the range over its buses' next stops (the
 *  schedule each wait is judged against), or `fallback` (the route's hourly) without them. */
export function scheduleRange(buses: Vehicle[], fallback: number | null): [number, number] | null {
  const s = buses.filter((b) => !b.at_terminal && b.sched_s != null).map((b) => Math.round(b.sched_s! / 60) * 60);
  if (!s.length) return fallback == null ? null : [fallback, fallback];
  return [Math.min(...s), Math.max(...s)];
}

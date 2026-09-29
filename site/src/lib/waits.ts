// One definition of "long wait" and "arriving together" for every view (the line, Right now,
// the bus card). The live feed measures each bus's wait at its next stop, counting every bus
// of the route and direction (short turns and branches too), and carries the schedule there
// (ghost_ledger.live). Replaying Sep 27-28, every long wait this flags was real but for
// tracker lag (a bus had passed, its report not yet in).
//   long wait : over twice the stop's scheduled gap, with at least two scheduled buses due
//               since the last one came: buses were due and didn't come. (The first bus after
//               a midday or overnight break has waited hours with nothing due: not a wait.)
//   together  : under a quarter of the scheduled gap after the bus before it
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

export const isTogether = (w: Wait): boolean =>
  w.s != null && (w.sched ? w.s < 0.25 * w.sched : w.s <= FALLBACK_TOGETHER_S);

/** Two buses of a route heading for the same next stop measure the same hole there: the
 *  wait is one, and belongs to the bus that gets there first. */
export const sameNextStop = (a: Vehicle, b: Vehicle): boolean =>
  a.next_stpid != null && a.next_stpid === b.next_stpid && a.rt === b.rt && a.dir === b.dir;

/** The bus behind `bus`: the nearest one whose last bus (at its next stop) was `bus`. */
export function behindOf(bus: Vehicle, vehicles: Vehicle[]): Vehicle | undefined {
  if (bus.stop_gap_s === undefined) return vehicles.find((v) => v.ahead === bus.vid && v.pid === bus.pid);
  return vehicles
    .filter((v) => v.last_by === bus.vid && v.stop_gap_s != null && !v.at_terminal)
    .sort((a, b) => a.stop_gap_s! - b.stop_gap_s!)[0];
}

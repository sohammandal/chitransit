// The claims every view shows (the line, Right now, the bus card). The poller decides them
// once (defined in the ghost_ledger.live docstring) and the feed carries them: long_wait,
// together_with, behind_vid/behind_stops. This file only reads them; a feed without them (an
// older poller) falls back to the rules as they were: long wait over twice the stop's
// schedule with two buses due, together by the same-pattern gap, no bus behind.
import type { Vehicle } from "./types";

export type Wait = {
  s: number | null; // seconds since the last bus passed (null: can't tell)
  by: string | null; // the bus that did
  at: string | null; // where: the stop the wait is measured at
  sched: number | null; // scheduled seconds between buses there (null: no judgment)
  due: number | null; // scheduled buses due there since the last one (null: not known)
  long?: boolean; // the poller's verdict (long_wait), when the feed has it
};

const FALLBACK_TOGETHER_S = 120; // when nothing says how often buses should come

export function waitOf(v: Vehicle, routeGap: number | null): Wait {
  if (v.stop_gap_s === undefined) return { s: v.gap_s, by: v.ahead, at: v.near ?? null, sched: routeGap, due: null };
  // Schedule fields both empty: the poller has no schedule (use the route's); sched_s empty with
  // sched_due known: nothing was scheduled there this hour, so no judgment.
  const sched = v.sched_s ?? (v.sched_due == null ? routeGap : null);
  return { s: v.stop_gap_s, by: v.last_by ?? null, at: v.next_stop ?? null, sched, due: v.sched_due ?? null, long: v.long_wait };
}

export const isLongWait = (w: Wait): boolean =>
  w.long ?? (w.s != null && w.sched != null && w.s > 2 * w.sched && (w.due == null || w.due >= 2));

/** The bus this one is together with, or null (the poller's together_with: both passed this
 *  bus's last stop under a quarter of the schedule apart, any pattern). */
export function togetherWith(v: Vehicle, routeGap: number | null): string | null {
  if (v.at_terminal) return null;
  if (v.together_with !== undefined) return v.together_with;
  if (v.gap_s == null || !v.ahead) return null;
  const sched = waitOf(v, routeGap).sched;
  return (sched ? v.gap_s < 0.25 * sched : v.gap_s <= FALLBACK_TOGETHER_S) ? v.ahead : null;
}

/** Two buses of a route heading for the same next stop measure the same hole there: the
 *  wait is one, and belongs to the bus that gets there first. */
export const sameNextStop = (a: Vehicle, b: Vehicle): boolean =>
  a.next_stpid != null && a.next_stpid === b.next_stpid && a.rt === b.rt && a.dir === b.dir;

/** The bus behind `bus` and how many of `bus`'s stops back it is (the poller's behind_vid,
 *  behind_stops: the nearest bus that will stop at `bus`'s next stop). None without one, or
 *  with an older feed. */
export function behindOf(bus: Vehicle, vehicles: Vehicle[]): { bus: Vehicle; stops: number } | undefined {
  const b = bus.behind_vid ? vehicles.find((v) => v.vid === bus.behind_vid) : undefined;
  return b && bus.behind_stops != null ? { bus: b, stops: bus.behind_stops } : undefined;
}

/** The scheduled gap a direction's header shows: the range over its buses' next stops (the
 *  schedule each wait is judged against), or `fallback` (the route's hourly) without them. */
export function scheduleRange(buses: Vehicle[], fallback: number | null): [number, number] | null {
  const s = buses.filter((b) => !b.at_terminal && b.sched_s != null).map((b) => Math.round(b.sched_s! / 60) * 60);
  if (!s.length) return fallback == null ? null : [fallback, fallback];
  return [Math.min(...s), Math.max(...s)];
}

// "Right now" problems, judged against the schedule like the nightly metrics:
//   together  : buses arriving less than a quarter of the scheduled gap apart (bunched)
//   long wait : a gap over twice the scheduled one
// A bus's gap_s (from the live feed) is how long ago the bus ahead passed the spot where it
// is now. Buses are compared only with others on the same pattern (pid), and buses laying
// over at a terminal are left out: they aren't serving riders.
import { chicago } from "./format";
import type { Headways, Vehicle } from "./types";

export type Problem = {
  kind: "together" | "wait";
  rt: string;
  dir: string;
  pid: number;
  buses: Vehicle[]; // together: the buses in the group; wait: the bus at the end of the hole
  wait_s: number | null; // together: the wait for the next bus after the group; wait: the hole
  scheduled_s: number | null;
};

const FALLBACK_TOGETHER_S = 120; // when a route has no scheduled gap for this hour

/** Scheduled seconds between buses for a route and direction at a moment, or null. */
export function scheduledGap(h: Headways | null, rt: string, dir: string | null, at: Date): number | null {
  if (!h || !dir) return null;
  const { date, hour } = chicago(at);
  const minutes = h[date]?.[`${rt}|${dir.replace("bound", "")}`]?.[String(hour)];
  return minutes ? minutes * 60 : null;
}

export function isTogether(v: Vehicle, scheduled: number | null): boolean {
  if (v.gap_s == null) return false;
  return scheduled ? v.gap_s < 0.25 * scheduled : v.gap_s <= FALLBACK_TOGETHER_S;
}

export function isLongWait(v: Vehicle, scheduled: number | null): boolean {
  return v.gap_s != null && scheduled != null && v.gap_s > 2 * scheduled;
}

/** Every problem on the road, worst first (the wait riders face relative to the schedule). */
export function findProblems(vehicles: Vehicle[], gapFor: (v: Vehicle) => number | null): Problem[] {
  const byPattern = new Map<number, Vehicle[]>();
  for (const v of vehicles) {
    if (v.pid == null || v.pdist == null || v.at_terminal) continue;
    const list = byPattern.get(v.pid);
    if (list) list.push(v);
    else byPattern.set(v.pid, [v]);
  }
  const problems: Problem[] = [];
  for (const [pid, buses] of byPattern) {
    buses.sort((a, b) => (b.pdist ?? 0) - (a.pdist ?? 0)); // the bus furthest along first
    let i = 0;
    while (i < buses.length) {
      let j = i;
      while (j + 1 < buses.length && isTogether(buses[j + 1], gapFor(buses[j + 1]))) j++;
      const next = buses[j + 1];
      const lead = buses[i];
      const scheduled = gapFor(lead);
      if (j > i) {
        problems.push({
          kind: "together", rt: lead.rt, dir: lead.dir ?? "", pid, buses: buses.slice(i, j + 1),
          wait_s: next?.gap_s ?? null, scheduled_s: scheduled,
        });
      } else if (next && isLongWait(next, gapFor(next))) {
        problems.push({
          kind: "wait", rt: next.rt, dir: next.dir ?? "", pid, buses: [next],
          wait_s: next.gap_s, scheduled_s: gapFor(next),
        });
      }
      i = j + 1;
    }
  }
  const severity = (p: Problem) => (p.wait_s ?? 0) / (p.scheduled_s ?? 600);
  return problems.sort((a, b) => severity(b) - severity(a));
}

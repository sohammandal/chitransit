// "Right now" problems: buses arriving together, and long waits (rules: lib/waits.ts).
import { chicago } from "./format";
import type { Headways, Vehicle } from "./types";
import { isLongWait, sameNextStop, togetherWith, waitOf } from "./waits";

export type Problem = {
  kind: "together" | "wait";
  rt: string;
  dir: string;
  pid: number;
  buses: Vehicle[]; // together: the buses in the group; wait: the bus at the end of the hole
  wait_s: number | null; // the long wait (at the lead bus's next stop); a bunch: only if one came before it
  scheduled_s: number | null;
  where: string | null; // together: near the lead bus; wait: the stop the wait is measured at
};

/** Scheduled seconds between buses for a route and direction at a moment, or null. */
export function scheduledGap(h: Headways | null, rt: string, dir: string | null, at: Date): number | null {
  if (!h || !dir) return null;
  const { date, hour } = chicago(at);
  const minutes = h[date]?.[`${rt}|${dir.replace("bound", "")}`]?.[String(hour)];
  return minutes ? minutes * 60 : null;
}

/** Every problem on the road, worst first (the wait riders face relative to the schedule).
 *  Rules: lib/waits.ts. Buses laying over at a terminal aren't serving riders and are left
 *  out. Buses together are chained pairwise (lib/waits.ts togetherWith: the poller's
 *  together_with, the bus just ahead at this bus's last stop). */
export function findProblems(vehicles: Vehicle[], gapFor: (v: Vehicle) => number | null): Problem[] {
  const live = vehicles.filter((v) => v.pid != null && v.pdist != null && !v.at_terminal);
  const byVid = new Map(live.map((v) => [v.vid, v]));
  const waits = new Map(live.map((v) => [v.vid, waitOf(v, gapFor(v))]));
  const sameLine = (a: Vehicle, b: Vehicle) => a.rt === b.rt && a.dir === b.dir;

  // Bunches: union the pairs (bus, the bus just before it at its next stop).
  const parent = new Map(live.map((v) => [v.vid, v.vid]));
  const root = (x: string): string => (parent.get(x) === x ? x : root(parent.get(x)!));
  const together = new Set<string>();
  for (const v of live) {
    const with_ = togetherWith(v), before = with_ ? byVid.get(with_) : undefined;
    if (before && sameLine(v, before)) {
      parent.set(root(v.vid), root(before.vid));
      together.add(v.vid).add(before.vid);
    }
  }
  const groups = new Map<string, Vehicle[]>();
  for (const v of live) if (together.has(v.vid)) groups.set(root(v.vid), [...(groups.get(root(v.vid)) ?? []), v]);

  const problems: Problem[] = [];
  // One wait per stop: of buses heading for the same next stop, the one furthest along
  const first = new Set<string>();
  for (const v of [...live].sort((a, b) => (b.pdist ?? 0) - (a.pdist ?? 0))) {
    if (![...first].some((f) => sameNextStop(byVid.get(f)!, v))) first.add(v.vid);
  }
  for (const group of groups.values()) {
    // The bus furthest along leads; a long wait in front of it is the bunch's number: the
    // classic story, a long gap and then buses together. Otherwise no number.
    const lead = group.find((v) => !group.some((m) => togetherWith(v) === m.vid)) ?? group[0];
    group.sort((a, b) => (a === lead ? -1 : b === lead ? 1 : (b.pdist ?? 0) - (a.pdist ?? 0)));
    const w = waits.get(lead.vid)!;
    problems.push({
      kind: "together", rt: lead.rt, dir: lead.dir ?? "", pid: lead.pid!, buses: group,
      // the hole is one: shown here only if no bus further along heads for the same stop
      wait_s: isLongWait(w) && first.has(lead.vid) ? w.s : null, scheduled_s: w.sched, where: lead.near,
    });
  }
  for (const v of live) {
    const w = waits.get(v.vid)!;
    if (together.has(v.vid) || !first.has(v.vid) || !isLongWait(w)) continue;
    problems.push({ kind: "wait", rt: v.rt, dir: v.dir ?? "", pid: v.pid!, buses: [v], wait_s: w.s, scheduled_s: w.sched, where: w.at });
  }
  const severity = (p: Problem) => (p.wait_s ?? 0) / (p.scheduled_s ?? 600);
  return problems.sort((a, b) => severity(b) - severity(a));
}

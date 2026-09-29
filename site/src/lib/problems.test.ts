import { describe, expect, it } from "vitest";
import { findProblems, scheduledGap } from "./problems";
import { behindOf, scheduleRange } from "./waits";
import type { Headways, Vehicle } from "./types";

// A bus as the live feed has it: `wait` s since `lastBy` passed its next stop, where buses are
// scheduled every 10 min and `due` were due since.
function bus(vid: string, pdist: number, wait: number | null, lastBy: string | null, extra: Partial<Vehicle> = {}): Vehicle {
  return {
    vid, rt: "49", pid: 1, hdg: 0, lat: 41.8, lon: -87.68, dly: false, des: "79th", dir: "Southbound",
    // gap_s/ahead (measured where the bus is, same pattern): as at its next stop unless given
    t: 0, pdist, gap_s: wait, ahead: lastBy, at_terminal: false, near: `Near ${vid}`,
    stop_gap_s: wait, last_by: lastBy, next_stop: `Stop ${vid}`, next_stpid: vid,
    sched_s: 600, sched_due: wait == null ? null : Math.floor(wait / 600), ...extra,
  };
}
const routeGap = () => 900; // the route's hourly schedule: only a fallback now

describe("findProblems", () => {
  it("groups buses under a quarter of the scheduled gap apart, with the wait after them", () => {
    // A leads; B is 90 s behind A at its next stop (under 150 s); C waited 40 min after B
    const p = findProblems([bus("A", 9000, null, null), bus("B", 8500, 90, "A"), bus("C", 1000, 2400, "B")], routeGap);
    expect(p).toHaveLength(1);
    expect(p[0]).toMatchObject({ kind: "together", wait_s: 2400, where: "Near A" });
    expect(p[0].buses.map((b) => b.vid)).toEqual(["A", "B"]);
  });

  it("judges together where the bus is, not at its next stop (#146, Sep 28)", () => {
    // Lake Shore Drive, no stops for miles: B passed Belmont 5 s ago, C is 2 miles back with
    // Belmont next ("5 s" there), but B passed C's spot 5 min ago: not together.
    const p = findProblems([bus("B", 36000, null, null), bus("C", 26000, 5, "B", { gap_s: 300, ahead: "B" })], routeGap);
    expect(p).toEqual([]);
    // measured where it is, 60 s behind: together
    const q = findProblems([bus("B", 36000, null, null), bus("C", 35000, 5, "B", { gap_s: 60, ahead: "B" })], routeGap);
    expect(q).toEqual([expect.objectContaining({ kind: "together" })]);
  });

  it("can't tell a short turn bunched with a full-route bus: no claim", () => {
    // gap_s is measured against the bus ahead on its own pattern; none within reach here
    const p = findProblems([bus("A", 9000, null, null), bus("S", 8600, 60, "A", { pid: 2, gap_s: null, ahead: null })], routeGap);
    expect(p).toEqual([]);
  });

  it("doesn't take a bus far ahead for the one behind a bunch (#95, Sep 28 23:20)", () => {
    // Westbound. 8334 (a short turn) and 8938 bunched at the east end; 8132 near the west end
    // last saw 8334 pass its stop 66 min ago, on 8334's previous trip.
    const wb = { dir: "Westbound", rt: "95", sched_s: 600 };
    const vs = [
      bus("8334", 6091, 1270, "1176", { ...wb, pid: 10956, lat: 41.727, lon: -87.566, gap_s: null, ahead: null }),
      bus("8938", 5883, 26, "8334", { ...wb, pid: 10956, lat: 41.727, lon: -87.568 }),
      bus("8132", 44601, 3960, "8334", { ...wb, pid: 10956, lat: 41.726, lon: -87.681, sched_s: 1800, sched_due: 3, gap_s: null, ahead: null }),
    ];
    const p = findProblems(vs, routeGap);
    expect(p.find((x) => x.kind === "together")).toMatchObject({ wait_s: null }); // unknown, not 66 min
    // and 8132's own wait stands, at its own stop and schedule
    expect(p.find((x) => x.kind === "wait")).toMatchObject({ wait_s: 3960, scheduled_s: 1800, where: "Stop 8132" });
    // a real bus behind them still counts
    const behind = bus("1111", 2000, 900, "8938", { ...wb, pid: 10956, lat: 41.727, lon: -87.55 });
    expect(findProblems([...vs, behind], routeGap).find((x) => x.kind === "together")).toMatchObject({ wait_s: 900 });
  });

  it("flags a long wait only when buses were due and didn't come, at the stop it's measured", () => {
    const due = findProblems([bus("A", 9000, null, null), bus("B", 3000, 1500, "A")], routeGap);
    expect(due).toEqual([expect.objectContaining({ kind: "wait", wait_s: 1500, where: "Stop B" })]);
    // 25 min but only one bus due since (say, service resumed after a break): not a long wait
    expect(findProblems([bus("A", 9000, null, null), bus("B", 3000, 1500, "A", { sched_due: 1 })], routeGap)).toEqual([]);
    // nothing scheduled there this hour: no judgment
    expect(findProblems([bus("A", 9000, null, null), bus("B", 3000, 5000, "A", { sched_s: null, sched_due: 0 })], routeGap)).toEqual([]);
  });

  it("names one hole once: two buses heading for the same stop measure the same wait", () => {
    const p = findProblems(
      [bus("A", 9000, null, null), bus("B", 5000, 1800, "A", { next_stpid: "X" }), bus("C", 4500, 1800, "A", { next_stpid: "X" })],
      routeGap,
    );
    expect(p.map((x) => [x.kind, x.buses[0].vid])).toEqual([["wait", "B"]]);
  });

  it("says nothing when it can't tell, and nothing on a healthy route", () => {
    expect(findProblems([bus("A", 9000, null, null), bus("B", 3000, null, null)], routeGap)).toEqual([]);
    const healthy = [bus("A", 9000, null, null), bus("B", 6000, 600, "A"), bus("C", 3000, 660, "B")];
    expect(findProblems(healthy, routeGap)).toEqual([]);
  });

  it("ignores buses laying over at a terminal", () => {
    const p = findProblems([bus("A", 9000, null, null), bus("B", 8900, 30, "A", { at_terminal: true })], routeGap);
    expect(p).toEqual([]);
  });

  it("puts the worst hole relative to its schedule first", () => {
    const p = findProblems(
      [
        bus("A", 9000, null, null), bus("B", 2000, 1800, "A"), // 30 min on a 10 min stop: 3x
        bus("X", 9000, null, null, { rt: "9" }), bus("Y", 2000, 1300, "X", { rt: "9" }), // 2.2x
      ],
      routeGap,
    );
    expect(p.map((x) => x.buses[0].vid)).toEqual(["B", "Y"]);
  });

  it("falls back to the same-pattern gap and the route's schedule with an older feed", () => {
    const old = (vid: string, pdist: number, gap: number | null, ahead: string | null): Vehicle => ({
      vid, rt: "49", pid: 1, hdg: 0, lat: 41.8, lon: -87.68, dly: false, des: "79th", dir: "Southbound",
      t: 0, pdist, gap_s: gap, ahead, at_terminal: false,
    });
    const p = findProblems([old("A", 9000, null, null), old("B", 8500, 90, "A"), old("C", 1000, 2400, "B")], () => 600);
    expect(p).toEqual([expect.objectContaining({ kind: "together", wait_s: 2400 })]);
    expect(findProblems([old("A", 9000, null, null), old("B", 3000, 1500, "A")], () => 600)[0]?.kind).toBe("wait");
  });
});

describe("scheduledGap", () => {
  const h: Headways = { "2026-09-27": { "49|South": { "14": 12 } } };
  it("reads the Chicago date and hour, matching direction names", () => {
    expect(scheduledGap(h, "49", "Southbound", new Date("2026-09-27T19:30:00Z"))).toBe(720); // 2:30 pm CDT
    expect(scheduledGap(h, "49", "Southbound", new Date("2026-09-27T21:30:00Z"))).toBeNull(); // 4:30 pm
    expect(scheduledGap(null, "49", "Southbound", new Date())).toBeNull();
  });
});

describe("behindOf and scheduleRange", () => {
  const v = (vid: string, extra: Partial<Vehicle>): Vehicle => ({
    vid, rt: "95", pid: 1, hdg: 0, lat: 41.72, lon: -87.6, dly: false, des: null, dir: "Westbound", t: 0,
    pdist: 0, gap_s: null, ahead: null, at_terminal: false, stop_gap_s: 60, last_by: null, sched_s: 600, sched_due: 0, ...extra,
  });
  it("finds the bus behind only if it is behind", () => {
    const a = v("A", { pdist: 5000 });
    expect(behindOf(a, [a, v("B", { pdist: 1000, last_by: "A" })])?.vid).toBe("B");
    expect(behindOf(a, [a, v("C", { pdist: 40000, last_by: "A" })])).toBeUndefined(); // ahead: an older pass
    // another pattern: by GPS along the direction (westbound: further west is ahead)
    expect(behindOf(a, [a, v("D", { pid: 2, lon: -87.7, last_by: "A" })])).toBeUndefined();
    expect(behindOf(a, [a, v("E", { pid: 2, lon: -87.55, last_by: "A" })])?.vid).toBe("E");
  });
  it("shows the stops' schedule range, or the route's without it", () => {
    expect(scheduleRange([v("A", {}), v("B", { sched_s: 1800 })], 900)).toEqual([600, 1800]);
    expect(scheduleRange([v("A", { sched_s: null, at_terminal: false })], 900)).toEqual([900, 900]);
    expect(scheduleRange([], null)).toBeNull();
  });
});

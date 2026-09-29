import { describe, expect, it } from "vitest";
import { findProblems, scheduledGap } from "./problems";
import type { Headways, Vehicle } from "./types";

// A bus as the live feed has it: `wait` s since `lastBy` passed its next stop, where buses are
// scheduled every 10 min and `due` were due since.
function bus(vid: string, pdist: number, wait: number | null, lastBy: string | null, extra: Partial<Vehicle> = {}): Vehicle {
  return {
    vid, rt: "49", pid: 1, hdg: 0, lat: 41.8, lon: -87.68, dly: false, des: "79th", dir: "Southbound",
    t: 0, pdist, gap_s: null, ahead: null, at_terminal: false, near: `Near ${vid}`,
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

  it("groups a short turn with the full-route bus it is bunched with", () => {
    const p = findProblems([bus("A", 9000, null, null), bus("S", 8600, 60, "A", { pid: 2 })], routeGap);
    expect(p).toEqual([expect.objectContaining({ kind: "together" })]);
    expect(p[0].buses.map((b) => b.vid).sort()).toEqual(["A", "S"]);
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

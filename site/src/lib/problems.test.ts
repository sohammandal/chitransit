import { describe, expect, it } from "vitest";
import { findProblems, scheduledGap } from "./problems";
import { bus as fake } from "./testing";
import { behindOf, scheduleRange } from "./waits";
import type { Headways, Vehicle } from "./types";

// A bus as the live feed has it: `wait` s since `lastBy` passed its next stop, where buses are
// scheduled every 10 min, with the poller's claims only when given (long_wait included).
const bus = (vid: string, pdist: number, wait: number | null, lastBy: string | null, extra: Partial<Vehicle> = {}) =>
  fake({
    vid, rt: "49", pdist, dir: "Southbound", near: `Near ${vid}`, stop_gap_s: wait, last_by: lastBy,
    next_stop: `Stop ${vid}`, next_stpid: vid, sched_s: 600, ...extra,
  });
const long = { long_wait: true };
const routeGap = () => 900; // the route's hourly schedule: only a fallback now

describe("findProblems", () => {
  it("shows a bunch the poller found, with the wait in front of it (the lead bus's)", () => {
    // A leads after a 30 min hole; B is together with A. C, far behind, has its own long wait
    const p = findProblems(
      [bus("A", 9000, 1800, "Z", long), bus("B", 8500, 90, "A", { together_with: "A" }), bus("C", 1000, 2400, "B", long)],
      routeGap,
    );
    const bunch = p.find((x) => x.kind === "together");
    expect(bunch).toMatchObject({ wait_s: 1800, scheduled_s: 600, where: "Near A" });
    expect(bunch!.buses.map((b) => b.vid)).toEqual(["A", "B"]);
    expect(p.filter((x) => x.kind === "wait").map((x) => x.buses[0].vid)).toEqual(["C"]); // another hole
  });

  it("names one hole once, a bunch included (#147 Sep 29: Foster, 5 miles without a stop)", () => {
    // L, furthest along, and the bunch B1+B2 behind it all head for Foster, 19 min since a bus
    const hole = { next_stpid: "F", sched_s: 360, sched_due: 3, long_wait: true };
    const p = findProblems(
      [bus("L", 19662, 1163, "Z", hole), bus("B1", 14219, 1163, "Z", hole), bus("B2", 11249, 1163, "Z", { ...hole, together_with: "B1" })],
      routeGap,
    );
    expect(p.filter((x) => x.wait_s != null).map((x) => [x.kind, x.buses[0].vid])).toEqual([["wait", "L"]]);
    expect(p.find((x) => x.kind === "together")).toMatchObject({ wait_s: null });
  });

  it("shows together only when the poller says so (#146, Sep 28: 5 s at the next stop, miles apart)", () => {
    expect(findProblems([bus("B", 36000, null, null), bus("C", 26000, 5, "B")], routeGap)).toEqual([]);
    const q = findProblems([bus("B", 36000, null, null), bus("C", 35000, 5, "B", { together_with: "B" })], routeGap);
    expect(q).toEqual([expect.objectContaining({ kind: "together" })]);
  });

  it("doesn't take a bus far ahead for the one behind a bunch (#95, Sep 28 23:20)", () => {
    // Westbound. 8334 (a short turn) and 8938 bunched at the east end; 8132 near the west end
    // last saw 8334 pass its stop 66 min ago, on 8334's previous trip.
    const wb = { dir: "Westbound", rt: "95", sched_s: 600 };
    const vs = [
      bus("8334", 6091, 1270, "1176", { ...wb, pid: 8143, long_wait: true }),
      bus("8938", 5883, 26, "8334", { ...wb, pid: 10956, together_with: "8334" }),
      bus("8132", 44601, 3960, "8334", { ...wb, pid: 10956, sched_s: 1800, sched_due: 3, long_wait: true }),
    ];
    const p = findProblems(vs, routeGap);
    // the bunch's number is its own lead's wait (8334's), never 8132's 66 min far ahead
    expect(p.find((x) => x.kind === "together")).toMatchObject({ wait_s: 1270 });
    expect(p.find((x) => x.kind === "together")!.buses[0].vid).toBe("8334");
    // and 8132's own wait stands, at its own stop and schedule
    expect(p.find((x) => x.kind === "wait")).toMatchObject({ wait_s: 3960, scheduled_s: 1800, where: "Stop 8132" });
  });

  it("trusts the poller's long_wait both ways, at the stop it's measured", () => {
    const due = findProblems([bus("A", 9000, null, null), bus("B", 3000, 1500, "A", long)], routeGap);
    expect(due).toEqual([expect.objectContaining({ kind: "wait", wait_s: 1500, where: "Stop B" })]);
    // 83 min, but the poller says no (say, service resumed after a break): no claim
    expect(findProblems([bus("A", 9000, null, null), bus("B", 3000, 5000, "A")], routeGap)).toEqual([]);
  });

  it("judges against the stop's schedule, none when nothing is scheduled there, else the route's", () => {
    const pair = (extra: Partial<Vehicle>) =>
      findProblems([bus("A", 9000, 300, "Z", extra), bus("B", 8900, 30, "A", { together_with: "A" })], routeGap)[0];
    expect(pair({})).toMatchObject({ kind: "together", scheduled_s: 600 });
    expect(pair({ sched_s: null, sched_due: 0 })).toMatchObject({ scheduled_s: null }); // nothing scheduled this hour
    expect(pair({ sched_s: null, sched_due: null })).toMatchObject({ scheduled_s: 900 }); // the poller has no schedule
  });

  it("names one hole once: two buses heading for the same stop measure the same wait", () => {
    const p = findProblems(
      [bus("A", 9000, null, null), bus("B", 5000, 1800, "A", { next_stpid: "X", ...long }), bus("C", 4500, 1800, "A", { next_stpid: "X", ...long })],
      routeGap,
    );
    expect(p.map((x) => [x.kind, x.buses[0].vid])).toEqual([["wait", "B"]]);
  });

  it("ignores buses laying over at a terminal", () => {
    const p = findProblems([bus("A", 9000, null, null), bus("B", 8900, 30, "A", { at_terminal: true })], routeGap);
    expect(p).toEqual([]);
  });

  it("puts the worst hole relative to its schedule first", () => {
    const p = findProblems(
      [
        bus("A", 9000, null, null), bus("B", 2000, 1800, "A", long), // 30 min on a 10 min stop: 3x
        bus("X", 9000, null, null, { rt: "9" }), bus("Y", 2000, 1300, "X", { rt: "9", ...long }), // 2.2x
      ],
      routeGap,
    );
    expect(p.map((x) => x.buses[0].vid)).toEqual(["B", "Y"]);
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
  const v = (vid: string, extra: Partial<Vehicle>) => fake({ vid, rt: "95", stop_gap_s: 60, sched_s: 600, sched_due: 0, ...extra });
  it("reads the bus behind from the feed: which bus, how many stops back", () => {
    const a = v("A", { behind_vid: "B", behind_stops: 3 }), b = v("B", {});
    expect(behindOf(a, [a, b])).toEqual({ bus: b, stops: 3 });
    expect(behindOf(v("A", { behind_vid: null, behind_stops: null }), [a, b])).toBeUndefined();
    expect(behindOf(v("A", { behind_vid: "gone", behind_stops: 1 }), [a, b])).toBeUndefined(); // not on the map
  });
  it("shows the stops' schedule range, or the route's without it", () => {
    expect(scheduleRange([v("A", {}), v("B", { sched_s: 1800 })], 900)).toEqual([600, 1800]);
    expect(scheduleRange([v("A", { sched_s: null, at_terminal: false })], 900)).toEqual([900, 900]);
    expect(scheduleRange([], null)).toBeNull();
  });
});

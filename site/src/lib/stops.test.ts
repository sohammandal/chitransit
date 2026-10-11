import { describe, expect, it } from "vitest";
import { routeStops, stopOnPattern, stopPatterns, stopRows } from "./stops";
import { bus as fake } from "./testing";
import type { Pattern, Stop, StopWaits, Vehicle } from "./types";

// Northbound on a straight street, a stop every 1,000 ft; Belmont at 5,000 ft
const pat = (rt: string, dir: string, names: string[]): Pattern => ({
  rt, dir, c: [],
  s: names.map((n, i) => [-87.65, 41.9 + i * 0.0027, n, i * 1000, 1, `${rt}-${n}`]),
});
const main = pat("22", "Northbound", ["A", "B", "C", "D", "E", "Belmont", "G", "H"]);
const short = pat("22", "Northbound", ["A", "B", "C", "D", "E", "Belmont"]); // ends at Belmont
const belmont: Stop = [-87.65, 41.9 + 5 * 0.0027, "Belmont", [1, 2]];
const patterns = new Map([[1, main], [2, short]]);

const bus = (vid: string, pid: number, pdist: number, extra: Partial<Vehicle> = {}) =>
  fake({ vid, rt: "22", pid, pdist, lat: 41.9, lon: -87.65, near: `near ${vid}`, next_stop: "x", ...extra });

describe("stopRows", () => {
  it("lists the next two buses that stop here, by stops away", () => {
    const rows = stopRows(belmont, [bus("far", 1, 500), bus("near", 1, 3500), bus("mid", 1, 1500), bus("past", 1, 6500)], patterns);
    expect(rows).toHaveLength(1);
    expect(rows[0].next.map((n) => [n.bus.vid, n.away, n.feet])).toEqual([["near", 2, 1500], ["mid", 4, 3500]]); // "past" went by; feet to Belmont
  });

  it("finds a corner spelled the other way round on another route's pattern", () => {
    const cross = pat("9", "Southbound", ["X", "Clark & Belmont", "Y"]);
    expect(stopOnPattern([-87.65, 41.9027, "Belmont & Clark", [3]], cross)).toBe(1);
  });

  it("skips a pattern whose last stop this is: nobody boards there", () => {
    expect(stopOnPattern(belmont, short)).toBeNull();
    expect(stopRows(belmont, [bus("S", 2, 3500)], patterns)[0].next).toEqual([]);
  });

  it("counts a bus at the stop as 0 away, and leaves out buses off their route", () => {
    const rows = stopRows(belmont, [bus("at", 1, 5000), bus("off", 1, 4000, { next_stop: null })], patterns);
    expect(rows[0].next.map((n) => [n.bus.vid, n.away])).toEqual([["at", 0]]);
  });

  it("includes a bus waiting to start its trip", () => {
    const rows = stopRows(belmont, [bus("L", 1, 0, { next_stop: null, trip: "starting", at_terminal: true })], patterns);
    expect(rows[0].next.map((n) => n.away)).toEqual([5]);
  });

  it("flags the next two coming together (the poller's claim)", () => {
    const rows = stopRows(belmont, [bus("a", 1, 3500), bus("b", 1, 3000, { together_with: "a" })], patterns);
    expect(rows[0].together).toBe(true);
    expect(stopRows(belmont, [bus("a", 1, 3500), bus("b", 1, 1000)], patterns)[0].together).toBe(false);
  });

  it("says how long since the last bus passed this stop: its own stamp, never a bus's wait at another stop (Oct 1)", () => {
    // #151 Oct 1 00:20: 8847's wait (49 min, at its next stop) was shown at Adams & Canal, where
    // 8894 had just passed. The stop's own stamp: 3 min there.
    const next = bus("a", 1, 1500, { stop_gap_s: 2940, long_wait: true }); // its next stop is C
    const waits: StopWaits = { generated_at: "", stops: { "22|Northbound": { "22-Belmont": [180, 0], "22-C": [2940, 1] } } };
    expect(stopRows(belmont, [next], patterns, waits)[0].wait).toEqual({ s: 180, long: false });
    expect(stopRows(belmont, [next], patterns, null)[0].wait).toBeNull(); // not loaded: say nothing
    const unknown: StopWaits = { generated_at: "", stops: { "22|Northbound": { "22-Belmont": [null, 0] } } };
    expect(stopRows(belmont, [next], patterns, unknown)[0].wait).toEqual({ s: null, long: false });
  });

  // Oct 10: CTA put #146's buses on reroute patterns the nightly didn't have, and every #146 stop
  // said "No bus on the way now" while they drove towards it.
  it("counts buses on a pattern the nightly doesn't list for the stop (a reroute's, from the live files)", () => {
    const reroute = pat("22", "Northbound", ["A", "B", "Q", "D", "E", "Belmont", "G"]); // C moved to Q
    const withReroute = new Map([...patterns, [9, reroute]]);
    const coming = [bus("r", 9, 1500)]; // on the reroute only
    expect(stopPatterns(belmont, coming, withReroute)).toEqual([1, 2, 9]);
    expect(stopRows(belmont, coming, withReroute)[0].next.map((n) => [n.bus.vid, n.away])).toEqual([["r", 4]]);
  });

  it("can't tell, rather than none coming, while a bus's pattern isn't loaded", () => {
    const [row] = stopRows(belmont, [bus("r", 9, 1500)], patterns); // pattern 9 not (yet) known
    expect([row.next, row.unknown]).toEqual([[], true]);
    expect(stopRows(belmont, [bus("a", 1, 1500), bus("r", 9, 1500)], patterns)[0].unknown).toBe(false); // one is coming
  });

  it("says CTA's reroute skips the stop when the route's buses run a pattern that skips it", () => {
    const skipping: Pattern = { ...pat("22", "Northbound", ["A", "B", "C", "D", "E", "G"]), x: [[-87.65, 41.9 + 5 * 0.0027, "Belmont", "22-Belmont"]] };
    const withSkip = new Map([...patterns, [9, skipping]]);
    const [row] = stopRows(belmont, [bus("r", 9, 500)], withSkip);
    expect([row.next, row.skipped, row.unknown]).toEqual([[], true, false]);
    // a reroute the nightly still lists for the stop, with no bus on it: not in force
    const listed: Stop = [belmont[0], belmont[1], belmont[2], [1, 2, 9]];
    expect(stopRows(listed, [], withSkip)[0].skipped).toBe(false);
  });
});

describe("routeStops", () => {
  it("draws a route's stops from its own patterns, merged by corner, with the nightly's other routes there", () => {
    const index: Stop[] = [[-87.65, 41.9 + 5 * 0.0027, "Belmont", [1, 40]]]; // #77 (40) stops there too
    const stops = routeStops([{ pid: 1, p: main }, { pid: 2, p: short }], index);
    expect(stops.map((s) => s.stop[2])).toEqual(["A", "B", "C", "D", "E", "Belmont", "G"]); // H: last stop
    expect(stops.find((s) => s.stop[2] === "Belmont")!.stop[3]).toEqual([1, 40]); // the short turn ends there
    expect(stops.every((s) => !s.skipped)).toBe(true);
  });

  it("adds the regular stops a reroute skips, marked, as CTA's map shows them", () => {
    const skipping: Pattern = { ...pat("22", "Northbound", ["A", "B", "Q"]), x: [[-87.64, 41.95, "Columbus & Balbo", "9"], [-87.65, 41.9, "A", "22-A"]] };
    const stops = routeStops([{ pid: 9, p: skipping }], []);
    expect(stops.map((s) => [s.stop[2], s.skipped])).toEqual([["A", false], ["B", false], ["Columbus & Balbo", true]]);
  });
});

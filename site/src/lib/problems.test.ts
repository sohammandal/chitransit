import { describe, expect, it } from "vitest";
import { findProblems, scheduledGap } from "./problems";
import type { Headways, Vehicle } from "./types";

// A bus on pattern 1, `pdist` feet along, `gap` seconds behind the bus ahead.
function bus(vid: string, pdist: number, gap: number | null, extra: Partial<Vehicle> = {}): Vehicle {
  return {
    vid, rt: "49", pid: 1, hdg: 0, lat: 41.8, lon: -87.68, dly: false, des: "79th", dir: "Southbound",
    t: 0, pdist, gap_s: gap, ahead: null, at_terminal: false, ...extra,
  };
}
const every10min = () => 600;

describe("findProblems", () => {
  it("groups buses under a quarter of the scheduled gap apart, with the wait after them", () => {
    // A leads; B is 90 s behind A (under 150 s = 25% of 10 min); C is 40 min behind B
    const p = findProblems([bus("A", 9000, null), bus("B", 8500, 90), bus("C", 1000, 2400)], every10min);
    expect(p).toHaveLength(1);
    expect(p[0]).toMatchObject({ kind: "together", wait_s: 2400 });
    expect(p[0].buses.map((b) => b.vid)).toEqual(["A", "B"]);
  });

  it("flags a wait over twice the scheduled gap, and nothing on a healthy route", () => {
    const healthy = findProblems([bus("A", 9000, null), bus("B", 6000, 600), bus("C", 3000, 660)], every10min);
    expect(healthy).toEqual([]);
    const p = findProblems([bus("A", 9000, null), bus("B", 3000, 1500)], every10min);
    expect(p).toEqual([expect.objectContaining({ kind: "wait", wait_s: 1500 })]);
  });

  it("judges against the route's own schedule: 20 min apart is fine every 15 min, long every 5", () => {
    const buses = [bus("A", 9000, null), bus("B", 3000, 1200)];
    expect(findProblems(buses, () => 900)).toEqual([]);
    expect(findProblems(buses, () => 300)[0]?.kind).toBe("wait");
  });

  it("ignores buses laying over at a terminal and buses on other patterns", () => {
    const p = findProblems(
      [bus("A", 9000, null), bus("B", 8900, 30, { at_terminal: true }), bus("C", 8800, 30, { pid: 2 })],
      every10min,
    );
    expect(p).toEqual([]);
  });

  it("puts the worst hole relative to its schedule first", () => {
    const p = findProblems(
      [
        bus("A", 9000, null), bus("B", 2000, 1800), // 30 min on a 10 min route: 3x
        bus("X", 9000, null, { pid: 2 }), bus("Y", 2000, 1300, { pid: 2 }), // 21.7 min: 2.2x
      ],
      every10min,
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

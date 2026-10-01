import { describe, expect, it } from "vitest";
import { onLine, project, withoutSpurs } from "./project";
import { bus } from "./testing";
import type { Pattern } from "./types";

// A straight line north along a street, stops every ~1,000 ft (0.00275° of latitude)
const line: Pattern = {
  rt: "9", dir: "Northbound",
  c: [[-87.66, 41.80], [-87.66, 41.81]],
  s: [0, 1, 2, 3].map((i) => [-87.66, 41.80 + 0.00275 * i, `Stop ${i}`, 1000 * i, 1] as [number, number, string, number, number]),
};

describe("project", () => {
  it("places a position on the line in feet along it, between its stops", () => {
    expect(project(line, 41.80 + 0.00275 * 1.5, -87.66)).toBeCloseTo(1500, -1);
    expect(project(line, 41.80 + 0.00275 * 2, -87.6603)).toBeCloseTo(2000, -1); // ~80 ft to the side
  });
  it("leaves out a bus on a parallel street (a branch that left the line)", () => {
    expect(project(line, 41.804, -87.655)).toBeNull(); // ~1,400 ft east
    expect(project(line, null, null)).toBeNull();
  });
});

describe("onLine", () => {
  const v = (vid: string, pid: number, lat: number, lon: number) => bus({ vid, pid, lat, lon, pdist: 1200 });
  it("draws the pattern's own buses as they are, others where they are on it, and counts the rest", () => {
    const { drawn, off } = onLine(line, 1, [v("A", 1, 0, 0), v("S", 2, 41.8055, -87.66), v("B", 3, 41.804, -87.655)]);
    expect(drawn.map((b) => [b.vid, b.other ?? false])).toEqual([["A", false], ["S", true]]);
    expect(drawn[1].pdist).toBeCloseTo(2000, -1);
    expect(off).toBe(1);
  });
});

describe("withoutSpurs", () => {
  it("drops a short out-and-back to a stop, keeps a real turnaround", () => {
    const A: [number, number] = [-87.77193, 41.89476], stop: [number, number] = [-87.77193, 41.8947]; // 7 m
    const end: [number, number] = [-87.76991, 41.89478];
    expect(withoutSpurs([[-87.77437, 41.89472], A, stop, A, end])).toEqual([[-87.77437, 41.89472], A, end]);
    const far: [number, number] = [-87.77193, 41.8940]; // 84 m up a street and back
    expect(withoutSpurs([A, far, A, end])).toEqual([A, far, A, end]);
  });
});

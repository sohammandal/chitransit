import { describe, expect, it } from "vitest";
import { feetBetween, milesText } from "./distance";
import { bus } from "./testing";
import type { Pattern } from "./types";

// A main pattern (stops A..E, 2,640 ft apart) and a short turn (A..C) of one route
const stop = (id: string, feet: number): Pattern["s"][number] => [-87.65, 41.9, id, feet, 1, id];
const main: Pattern = { rt: "66", dir: "Westbound", c: [], s: ["A", "B", "C", "D", "E"].map((x, i) => stop(x, i * 2640)) };
const short: Pattern = { rt: "66", dir: "Westbound", c: [], s: ["A", "B", "C"].map((x, i) => stop(x, 1000 + i * 2640)) };
const patterns = new Map([[1, main], [2, short]]);

describe("distances along the route", () => {
  it("reads in tenths of a mile, never as zero", () => {
    expect(milesText(11_088)).toBe("2.1 mi");
    expect(milesText(300)).toBe("under 0.1 mi");
  });

  it("measures the bus behind on the same pattern by its position", () => {
    const ahead = bus({ vid: "a", pid: 1, pdist: 9000, next_stpid: "D" });
    expect(feetBetween(ahead, bus({ vid: "b", pid: 1, pdist: 3720 }), patterns)).toBe(5280);
  });

  it("measures a bus on another pattern through the stop the bus ahead heads for", () => {
    // ahead on main, 1,000 ft short of C (5,280); behind on the short turn, 2,000 ft short of C (6,280 there)
    const ahead = bus({ vid: "a", pid: 1, pdist: 4280, next_stpid: "C" });
    expect(feetBetween(ahead, bus({ vid: "b", pid: 2, pdist: 4280 }), patterns)).toBe(1000);
  });

  it("says nothing it can't measure", () => {
    const ahead = bus({ vid: "a", pid: 1, pdist: 9000, next_stpid: "D" }); // D isn't on the short turn
    expect(feetBetween(ahead, bus({ vid: "b", pid: 2, pdist: 100 }), patterns)).toBeNull();
    expect(feetBetween(ahead, bus({ vid: "b", pid: 1, pdist: 9500 }), patterns)).toBeNull(); // not behind
    expect(feetBetween(ahead, bus({ vid: "b", pid: 1, pdist: null }), patterns)).toBeNull();
  });
});

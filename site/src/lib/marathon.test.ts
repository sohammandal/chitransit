import { describe, expect, it } from "vitest";
import { clock, slice } from "./marathon";

describe("clock", () => {
  it("says times as riders do", () => {
    expect(clock(6 * 60)).toBe("6 AM");
    expect(clock(12 * 60)).toBe("noon");
    expect(clock(15 * 60 + 40)).toBe("3:40 PM");
    expect(clock(0)).toBe("midnight");
    expect(clock(24 * 60 + 30)).toBe("12:30 AM");
  });
});

describe("slice", () => {
  // a straight course east along a street, a mile between points
  const course = { line: [[0, 0], [1, 0], [2, 0], [3, 0]] as [number, number][], at: [0, 1, 2, 3] };

  it("cuts the course between two miles, through the points between", () => {
    expect(slice(course, 0.5, 2.25)).toEqual([[0.5, 0], [1, 0], [2, 0], [2.25, 0]]);
  });

  it("keeps to the course: before the start is the start, past the finish the finish", () => {
    expect(slice(course, -1, 9)).toEqual([[0, 0], [1, 0], [2, 0], [3, 0]]);
  });
});

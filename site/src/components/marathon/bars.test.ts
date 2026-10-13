import { describe, expect, it } from "vitest";
import { barRows } from "./bars";

describe("barRows (the report's static bar charts)", () => {
  it("prints each value with its unit and scales the bars from zero to the largest", () => {
    const { rows, room } = barRows(
      [{ label: "Sep 27", value: 9.43 }, { label: "Oct 4", value: 9.67 }, { label: "Marathon Sunday", value: 9.63, highlight: true }],
      { unit: "min" },
    );
    expect(rows.map((r) => r.text)).toEqual(["9.4 min", "9.7 min", "9.6 min"]);
    expect(rows.map((r) => r.share)).toEqual([9.43 / 9.67, 1, 9.63 / 9.67]);
    expect(rows.map((r) => !!r.highlight)).toEqual([false, false, true]);
    expect(room).toBeCloseTo("9.4 min".length * 0.66 + 0.6);
  });

  it("draws a zero as no bar, groups thousands and puts a symbol right after the number", () => {
    const { rows } = barRows([{ label: "Sep 27", value: 0 }, { label: "Oct 4", value: 17 }, { label: "Marathon Sunday", value: 1403 }], { unit: "%" });
    expect(rows.map((r) => r.text)).toEqual(["0%", "17%", "1,403%"]);
    expect(rows[0].share).toBe(0);
    expect(rows[2].share).toBe(1);
  });

  it("takes an end of scale and a format of its own, and keeps bars inside the scale", () => {
    const { rows } = barRows([{ label: "a", value: 50 }, { label: "b", value: 150 }, { label: "c", value: -5 }], { max: 100, format: (v) => `${v} stops` });
    expect(rows.map((r) => r.text)).toEqual(["50 stops", "150 stops", "-5 stops"]);
    expect(rows.map((r) => r.share)).toEqual([0.5, 1, 0]);
  });

  it("all zeros: no bars and no division by zero", () => {
    expect(barRows([{ label: "a", value: 0 }, { label: "b", value: 0 }]).rows.map((r) => r.share)).toEqual([0, 0]);
  });
});

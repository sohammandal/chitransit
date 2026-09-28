import { describe, expect, it } from "vitest";
import { swarm } from "./swarm";

const G = 14;
const minDistance = (xs: number[], dy: number[]) =>
  Math.min(...xs.flatMap((a, i) => xs.slice(i + 1).map((b, j) => Math.hypot(a - b, dy[i] - dy[i + 1 + j]))));

describe("swarm", () => {
  it("leaves buses far apart on the line", () => {
    expect(swarm([10, 60, 200], G)).toEqual([0, 0, 0]);
  });
  it("stacks a pair at one spot, centred on the line", () => {
    expect(swarm([50, 50], G).sort()).toEqual([-7, 7]);
  });
  it("stacks three at one spot, centred", () => {
    expect(swarm([50, 50, 50], G).sort((a, b) => a - b)).toEqual([-14, 0, 14]);
  });
  it("zigzags a string of close buses instead of one tall tower (#66, Sep 28)", () => {
    // 8 buses, each ~10 px from the next: chained, they made one 8-high tower
    const xs = [40, 51, 60, 61, 72, 74, 86, 87];
    const dy = swarm(xs, G);
    expect(minDistance(xs, dy)).toBeGreaterThanOrEqual(G - 1e-6);
    expect(Math.max(...dy) - Math.min(...dy)).toBeLessThanOrEqual(3 * G); // not 7 * G
  });
  it("never overlaps, whatever the spacing", () => {
    for (let n = 0; n < 200; n++) {
      const xs = Array.from({ length: 12 }, () => Math.round(Math.random() * 120));
      expect(minDistance(xs, swarm(xs, G))).toBeGreaterThanOrEqual(G - 1e-6);
    }
  });
});

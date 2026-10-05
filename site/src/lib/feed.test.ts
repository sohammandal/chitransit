import { describe, expect, it } from "vitest";
import { nextFetch } from "./feed";

const gen = 1_000_000; // a file's generated_at (ms)

describe("nextFetch", () => {
  it("asks 5.5 s after the next file is due (when the CDN has it), not before", () => {
    expect(nextFetch(gen, true, true, gen + 2_000)).toBe(33_500);
  });
  it("got the old copy (the CDN still had it): asks again in 1.5 s, not 10", () => {
    expect(nextFetch(gen, true, false, gen + 36_000)).toBe(1_500);
  });
  it("a late poller or a failed fetch: back to the calm 10 s retry", () => {
    expect(nextFetch(gen, true, false, gen + 35_500 + 16_000)).toBe(10_000);
    expect(nextFetch(gen, false, false, gen + 34_000)).toBe(10_000);
    expect(nextFetch(null, true, true, gen)).toBe(10_000);
  });
});

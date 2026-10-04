import { beforeEach, describe, expect, it, vi } from "vitest";

/** A browser-ish global for one test: host, path, referrer, webdriver; returns the beacons. */
function browser(host: string, webdriver = false) {
  const sent: URL[] = [];
  const loc = { hostname: host, pathname: "/" };
  vi.stubGlobal("location", loc);
  vi.stubGlobal("document", { title: "t", referrer: "https://www.reddit.com/" });
  vi.stubGlobal("screen", { width: 390, height: 844 });
  vi.stubGlobal("window", { devicePixelRatio: 3 });
  vi.stubGlobal("navigator", { webdriver, sendBeacon: (u: string) => sent.push(new URL(u)) });
  return { sent, loc };
}

describe("countView", () => {
  beforeEach(() => vi.resetModules());

  it("counts each new page on the real site, with the referrer on the first only", async () => {
    const { sent, loc } = browser("chitransit.fyi");
    const { countView } = await import("./count");
    countView();
    countView(); // same page again: not counted
    loc.pathname = "/route/79/";
    countView();
    expect(sent.map((u) => u.searchParams.get("p"))).toEqual(["/", "/route/79/"]);
    expect(sent.map((u) => u.searchParams.get("r"))).toEqual(["https://www.reddit.com/", ""]);
    expect(sent[0].origin + sent[0].pathname).toBe("https://chitransit.goatcounter.com/count");
  });

  it("never counts dev, tests or headless browsers", async () => {
    for (const [host, wd] of [["localhost", false], ["127.0.0.1", false], ["chitransit.fyi", true]] as const) {
      vi.resetModules();
      const { sent } = browser(host, wd);
      (await import("./count")).countView();
      expect(sent).toEqual([]);
    }
  });
});

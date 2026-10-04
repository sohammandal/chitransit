// Every view of the app in a real browser, against the production build with fixed data
// (e2e/fixtures: a saved live feed and nightly files). The basemap still loads from the network.
import { readFileSync, existsSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const FIX = new URL("./fixtures/", import.meta.url);
const feed = JSON.parse(readFileSync(new URL("vehicles.json", FIX), "utf8"));
const fixture = (name: string) => (existsSync(new URL(name, FIX)) ? readFileSync(new URL(name, FIX)) : null);

type Map = {
  loaded(): boolean; getFilter(id: string): unknown; queryRenderedFeatures(o: object): { properties: { rt: string } }[];
  jumpTo(o: { center: [number, number]; zoom: number }): void; project(p: [number, number]): { x: number; y: number };
  getCanvas(): HTMLCanvasElement;
};
declare global { interface Window { map?: Map; __e2e?: boolean } }

/** Serve the fixtures (or 404 for the files in `missing`, or another feed `f`), freeze the clock
 *  just after the feed. */
async function setUp(page: Page, missing: string[] = [], f = feed) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/openfreemap|Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.clock.setFixedTime(new Date(new Date(f.generated_at).getTime() + 10_000));
  await page.addInitScript((f) => {
    window.__e2e = true;
    (window as unknown as { __feed: unknown }).__feed = f;
    // every Content-Security-Policy violation, checked after each test (afterEach below)
    const csp: string[] = ((window as unknown as { __csp: string[] }).__csp = []);
    document.addEventListener("securitypolicyviolation", (e) => csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  }, f);
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.route("**/data/v1/**", (r) => {
    const name = new URL(r.request().url()).pathname.replace("/data/v1/", "");
    const body = missing.includes(name) ? null : fixture(name);
    return body ? r.fulfill({ body, contentType: "application/json" }) : r.fulfill({ status: 404 });
  });
  return errors;
}

// The page's Content-Security-Policy (a meta tag the build writes) blocked nothing in any test.
test.afterEach(async ({ page }) => {
  const csp = await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? []).catch(() => []);
  expect(csp, "CSP violations").toEqual([]);
});

/** The live search box: once the app has replaced the static first-paint shell (whose copy
 *  is inert: people can't type in it, but a test's fill() could, and lose the text). */
async function searchBox(page: Page) {
  await expect(page.locator(".shell")).toHaveCount(0);
  return page.getByLabel("Go to a route");
}

async function busesDrawn(page: Page) {
  await page.waitForFunction(() => {
    const m = window.map;
    return !!m && m.loaded() && m.queryRenderedFeatures({ layers: ["bus-dot", "bus-sym"] }).length > 0;
  }, undefined, { timeout: 30_000 });
}

async function noSideScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("city: live count, right-now list, buses on the map @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await expect(page.getByText(`${feed.vehicles.length} buses on the road`)).toBeVisible();
  await expect(page.getByText("Updated 10 s ago")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  expect(await page.locator(".sheet li").count()).toBeGreaterThan(0);
  await busesDrawn(page);
  await noSideScroll(page);
  expect(errors).toEqual([]);
});

test("route: lines per direction, key, map shows only this route @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await expect(page.getByRole("heading", { name: "Chicago", exact: true })).toBeVisible();
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await expect(page.getByText("Together", { exact: true })).toBeVisible();

  await busesDrawn(page);
  expect(await page.evaluate(() => window.map!.getFilter("bus-dot"))).toEqual(["==", ["get", "rt"], "66"]);
  const shown = await page.evaluate(() => window.map!.queryRenderedFeatures({ layers: ["bus-dot", "bus-sym"] }).map((f) => f.properties.rt));
  expect(new Set(shown)).toEqual(new Set(["66"]));
  await noSideScroll(page);
  expect(errors).toEqual([]);
});

test("about: opens and closes", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await page.getByRole("button", { name: "About" }).click();
  await expect(page.getByText(/not affiliated/)).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
});

test("report card: shares add up to 100%, split matches, definitions shown @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await page.getByRole("tab", { name: "Report Card" }).click();
  await expect(page.getByText("Last 1 day")).toBeVisible();
  await expect(page.getByText("373 trips scheduled")).toBeVisible();
  const figs = (await page.locator(".figs dd").allInnerTexts()).map((t) => Number(t.replace(/[^\d.]/g, "")));
  expect(figs.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 0);
  // #66 on Sep 26: 373 scheduled, 346 ran, 6 canceled with notice, 2 proven, 19 couldn't tell
  expect(figs).toEqual([92.8, 2.1, 5.1]);
  const tops = await page.locator(".figs dd").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)));
  expect(new Set(tops).size).toBe(1); // the three numbers sit on one line
  await expect(page.getByText("At least 8 trips didn't come: 6 canceled with notice, 2 never came with no notice")).toBeVisible();
  await page.getByRole("tab", { name: "Now" }).click();
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  expect(errors).toEqual([]);
});

test("nightly files missing: the live parts still work", async ({ page }) => {
  const errors = await setUp(page, ["routes.json", "headways.json", "report.json"]);
  await page.goto("/route/66/");
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await page.getByRole("tab", { name: "Report Card" }).click();
  await expect(page.getByText(/No report for this route yet/)).toBeVisible();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("phone: the sheet never covers the top bar", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout only");
  await setUp(page);
  await page.goto("/route/66/");
  const grab = page.getByRole("button", { name: /Show more|Show less/ });
  for (let i = 0; i < 3; i++) {
    await grab.click();
    await page.waitForTimeout(350); // the sheet animates its height
    const bar = await page.locator("header.bar").boundingBox();
    const sheet = await page.locator(".sheet").boundingBox();
    expect(sheet!.y).toBeGreaterThanOrEqual(bar!.y + bar!.height);
  }
});

test("phone: dragging the handle resizes the sheet, and it settles on a size", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout only");
  await setUp(page);
  await page.goto("/");
  await page.getByRole("button", { name: "About" }).click(); // long enough to need "full"
  const grab = page.getByRole("button", { name: /Show more|Show less/ });
  const sheet = page.locator(".sheet");
  await expect(sheet).toHaveClass(/half/);
  const drag = async (dy: number) => {
    const b = (await grab.boundingBox())!;
    const x = b.x + b.width / 2, y = b.y + b.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    for (let i = 1; i <= 8; i++) await page.mouse.move(x, y + (dy * i) / 8);
    await page.mouse.up();
    await page.waitForTimeout(350); // the sheet animates to its size
  };
  await drag(-150);
  await expect(sheet).toHaveClass(/full/);
  const bar = (await page.locator("header.bar").boundingBox())!;
  expect((await sheet.boundingBox())!.y).toBeGreaterThanOrEqual(bar.y + bar.height);
  await drag(400);
  await expect(sheet).toHaveClass(/peek/);
  // A short flick still moves one size. Sent from inside the page so its speed doesn't depend
  // on how busy the test machine is (a slow 60 px drag rightly snaps back).
  await grab.evaluate(async (g) => {
    const r = g.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
    const send = (type: string, dy: number) =>
      (type === "pointerdown" ? g : window).dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: x, clientY: y - dy, pointerId: 9, isPrimary: true }));
    send("pointerdown", 0);
    for (let i = 1; i <= 6; i++) { await new Promise((r) => setTimeout(r, 8)); send("pointermove", i * 10); }
    send("pointerup", 60);
  });
  await page.waitForTimeout(350);
  await expect(sheet).toHaveClass(/half/);
  await grab.click(); // a tap still steps
  await expect(sheet).toHaveClass(/full/);
  await grab.click();
  await expect(sheet).toHaveClass(/peek/);
});

test("phone: the title and tabs stay put and drag the sheet; only the rest scrolls", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout only");
  await setUp(page);
  await page.goto("/");
  await page.getByRole("button", { name: "About" }).click(); // long: scrolls at half
  const sheet = page.locator(".sheet");
  await expect(sheet).toHaveClass(/half/);
  await page.waitForTimeout(400); // the sheet animates to its size
  const head = page.locator(".sheet-head");
  const before = (await head.boundingBox())!.y;
  await page.locator(".sheet .body").evaluate((b) => (b.scrollTop = 200));
  await page.waitForTimeout(100);
  expect(Math.abs((await head.boundingBox())!.y - before)).toBeLessThan(1);
  // Drag from the title: the sheet grows.
  const title = (await head.locator("h2").boundingBox())!;
  const x = title.x + 20, y = title.y + title.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y - 250, { steps: 8 });
  await page.mouse.up();
  await expect(sheet).toHaveClass(/full/);
});

test("phone: a drag that starts on a tab resizes the sheet, a tap switches tabs", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout only");
  await setUp(page);
  await page.goto("/route/66/");
  const sheet = page.locator(".sheet");
  await expect(sheet).toHaveClass(/half/);
  const tab = page.getByRole("tab", { name: "Report Card" });
  const b = (await tab.boundingBox())!;
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + 200, { steps: 8 });
  await page.mouse.up();
  await expect(sheet).toHaveClass(/peek/);
  await expect(tab).toHaveAttribute("aria-selected", "false");
  await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
});

test("phone: a tap skips the full size when the content already fits", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout only");
  await setUp(page);
  // short in the fixtures: fits in half on a tall phone (half is 50% of the screen since Oct 4)
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.goto("/route/66/");
  const grab = page.getByRole("button", { name: /Show more|Show less/ });
  const sheet = page.locator(".sheet");
  await expect(sheet).toHaveClass(/half/);
  await grab.click();
  await expect(sheet).toHaveClass(/peek/);
});

test("search: a plain search field (no contact autofill on iOS)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  const input = (await searchBox(page));
  await expect(input).toHaveAttribute("type", "search");
  await expect(input).toHaveAttribute("autocomplete", "off");
  // Safari offers contacts when a field's name or id mentions "name"
  for (const a of ["name", "id"]) expect((await input.getAttribute(a)) ?? "").not.toMatch(/name/i);
});

test("my location button is on screen and not covered by the panel @both", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  await busesDrawn(page);
  const button = page.getByRole("button", { name: "Find my location" });
  await expect(button).toBeVisible();
  const box = (await button.boundingBox())!;
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest("button")?.getAttribute("aria-label"), [box.x + box.width / 2, box.y + box.height / 2]);
  expect(hit).toBe("Find my location");
});

test("map buttons: credits and my-location are the same size, on one edge @both", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const locate = (await page.getByRole("button", { name: "Find my location" }).boundingBox())!;
  const credits = (await page.locator(".maplibregl-ctrl-attrib-button").boundingBox())!;
  expect(Math.round(credits.width)).toBe(Math.round(locate.width));
  expect(Math.round(credits.height)).toBe(Math.round(locate.height));
  expect(Math.round(credits.x + credits.width)).toBe(Math.round(locate.x + locate.width));
});

test("search: typing 66 at normal speed opens #66 (never #6), and Back returns to the city", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await (await searchBox(page)).pressSequentially("66", { delay: 80 });
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await expect(page.getByRole("heading", { name: "Chicago", exact: true })).toBeVisible();
  await (await searchBox(page)).blur();
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
});

test("search: a pause after 6 shows #6, then 66 replaces it in history", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  const box = (await searchBox(page));
  await box.pressSequentially("6");
  await expect(page).toHaveURL(/\/route\/6\/$/);
  await box.pressSequentially("6");
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await box.blur();
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
});

test("search: names suggest routes; tapping one opens it", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await (await searchBox(page)).fill("western");
  await page.getByRole("button", { name: /49B North Western/ }).click();
  await expect(page).toHaveURL(/\/route\/49B\/$/);
});

/** Tap the line's track at a spot no bus covers (a bus tap would open the bus instead). */
async function tapLineFree(page: Page, line: ReturnType<Page["locator"]>) {
  const track = (await line.locator(".track").boundingBox())!;
  for (const f of [0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9]) {
    const x = track.x + track.width * f, y = track.y + track.height / 2;
    const cls = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.getAttribute("class") ?? "", [x, y]);
    if (/pickable|track|tick|express/.test(cls)) return void (await page.mouse.click(x, y));
  }
  throw new Error("no bus-free spot on the line");
}

/** How many of a route's buses are outside the map's current view. */
async function offScreen(page: Page, rt: string) {
  await page.waitForTimeout(1200); // let the map finish easing
  return page.evaluate((rt) => {
    const m = window.map as unknown as { getBounds(): { contains(p: [number, number]): boolean } };
    const feed = (window as unknown as { __feed: { vehicles: { rt: string; lon: number; lat: number }[] } }).__feed;
    return feed.vehicles.filter((v) => v.rt === rt && !m.getBounds().contains([v.lon, v.lat])).length;
  }, rt);
}

test("map fits every route opened in a row: search, close, search again, then a list row @both", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const box = (await searchBox(page));

  await box.fill("66");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/route\/66\/$/);
  expect(await offScreen(page, "66")).toBe(0);

  await page.getByLabel("Back to all routes").click();
  await box.fill("151");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/route\/151\/$/);
  expect(await offScreen(page, "151")).toBe(0);

  await page.getByLabel("Back to all routes").click();
  const row = page.locator(".sheet li button").first();
  const rt = (await row.locator(".rt").innerText()).trim();
  await row.click();
  await expect(page).toHaveURL(new RegExp(`/route/${rt}/$`));
  expect(await offScreen(page, rt)).toBe(0);
  await page.goBack(); // Back returns to the city
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
});

test("map fits on route-to-route search, browser Back, and Forward @both", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const box = (await searchBox(page));
  await box.fill("66");
  await box.press("Enter");
  await box.fill("151"); // straight from #66, no close
  await box.press("Enter");
  await expect(page).toHaveURL(/\/route\/151\/$/);
  expect(await offScreen(page, "151")).toBe(0);
  await page.goBack();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  expect(await offScreen(page, "66")).toBe(0);
  await page.goForward();
  await expect(page).toHaveURL(/\/route\/151\/$/);
  expect(await offScreen(page, "151")).toBe(0);
});

test("search suggestions float over the map: the top bar keeps its height @both", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  const bar = page.locator("header.bar");
  await expect(bar.getByText(/buses on the road/)).toBeVisible(); // the app, not the static shell
  const before = (await bar.boundingBox())!.height;
  // "western" fits several routes, so nothing auto-opens and the list stays up (a lone "6"
  // opens #6 after the pause and closes the list, which raced on slower CI machines).
  await (await searchBox(page)).pressSequentially("western");
  await expect(page.locator("#route-list")).toBeVisible();
  expect((await bar.boundingBox())!.height).toBe(before);
});

test("tapping a bus on the map opens it without moving the map; the back link fits its route @both", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  // Zoom into one #66 bus, as a rider looking at their own street would.
  const bus = feed.vehicles.find((v: { rt: string; at_terminal: boolean }) => v.rt === "66" && !v.at_terminal);
  await page.evaluate(([lon, lat]) => (window.map as unknown as { jumpTo(o: object): void }).jumpTo({ center: [lon, lat], zoom: 15 }), [bus.lon, bus.lat]);
  await page.waitForTimeout(800);
  const camera = () => page.evaluate(() => {
    const m = window.map as unknown as { getCenter(): { lng: number; lat: number }; getZoom(): number };
    return { lng: m.getCenter().lng.toFixed(4), lat: m.getCenter().lat.toFixed(4), zoom: m.getZoom().toFixed(2) };
  });
  const before = await camera();
  const at = await page.evaluate(([lon, lat]) => (window.map as unknown as { project(p: [number, number]): { x: number; y: number } }).project([lon, lat]), [bus.lon, bus.lat]);
  await page.mouse.click(at.x, at.y);

  await expect(page.getByRole("button", { name: "‹ 66 Chicago" })).toBeVisible();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await page.waitForTimeout(1000);
  const after = await camera();
  expect(after.zoom).toBe(before.zoom); // no zoom-out to the whole route
  // (the map may pan a little to keep the bus clear of the panel, never zoom)

  await page.getByRole("button", { name: "‹ 66 Chicago" }).click();
  expect(await offScreen(page, "66")).toBe(0); // the route view fits the whole route
});

test("tapping a bus on the line zooms the map to it; the back link returns to the whole route @both", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  await busesDrawn(page);
  expect(await offScreen(page, "66")).toBe(0);
  await page.locator(".sheet .bus").nth(2).click();
  await expect(page.getByRole("button", { name: "‹ 66 Chicago" })).toBeVisible();
  await page.waitForTimeout(1200);
  const view = await page.evaluate(() => {
    const m = window.map as unknown as { getZoom(): number; getBounds(): { contains(p: [number, number]): boolean } };
    const feed = (window as unknown as { __feed: { vehicles: { vid: string; lon: number; lat: number }[] } }).__feed;
    const vid = document.querySelector(".facts li")!.textContent!.replace("Bus ", "").trim();
    const bus = feed.vehicles.find((v) => v.vid === vid)!;
    return { zoom: m.getZoom(), inView: m.getBounds().contains([bus.lon, bus.lat]) };
  });
  expect(view.zoom).toBeGreaterThanOrEqual(15);
  expect(view.inView).toBe(true);

  await page.getByRole("button", { name: "‹ 66 Chicago" }).click();
  expect(await offScreen(page, "66")).toBe(0);
});

test("line view: a string of close buses isn't drawn as one tower (#66, Sep 28 16:35)", async ({ page }) => {
  // #66 westbound at rush hour, replayed from Ingress: 8 buses over 1.5 miles of crawling
  // traffic downtown (2,610 → 10,289 ft), each < 0.42 mi from the next. They chained into one
  // 8-high stack with a real 7.6-min wait bar (8181 → 8178) drawn inside it.
  const wb: [string, number, number | null, string | null][] = [
    ["8653", 42752, 757, null], ["8117", 41316, 132, "8653"], ["8684", 32449, 571, "8117"],
    ["8169", 31933, 70, "8684"], ["8601", 31932, 42, "8169"], ["1243", 30555, 112, "8601"],
    ["1354", 28756, 186, "1243"], ["8186", 21743, 634, "1354"], ["8610", 12253, 1218, "8186"],
    ["1250", 10289, 256, "8610"], ["8178", 10183, 205, "1250"], ["8181", 8375, 520, "8178"],
    ["8818", 8091, 81, "8181"], ["8118", 6658, 338, "8818"], ["8776", 6498, 80, "8118"],
    ["8184", 4364, 267, "8776"], ["8101", 2610, 263, "8184"],
  ];
  const base = feed.vehicles.find((v: { pid: number }) => v.pid === 6665);
  const rush = { ...feed, vehicles: [
    ...feed.vehicles.filter((v: { rt: string; pid: number }) => !(v.rt === "66" && v.pid === 6665)),
    // the poller's claims: long over twice the schedule, together under a quarter of it
    ...wb.map(([vid, pdist, gap, by]) => ({ ...base, vid, pdist, at_terminal: false, stop_gap_s: gap, last_by: by, next_stpid: vid,
      long_wait: gap != null && gap > 2 * base.sched_s, together_with: gap != null && gap < base.sched_s / 4 ? by : null })),
  ] };
  await setUp(page);
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: rush }));
  await page.goto("/route/66/");
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  const svg = page.locator(".sheet svg").filter({ has: page.locator(`[aria-label="Bus 8181"]`) });
  const dots = await svg.locator("circle.dot").evaluateAll((cs) => cs.map((c) => [Number(c.getAttribute("cx")), Number(c.getAttribute("cy"))]));
  expect(dots.length).toBe(wb.length);
  const ys = dots.map((d) => d[1]);
  expect(Math.max(...ys) - Math.min(...ys)).toBeLessThanOrEqual(42); // at most 3 rows; was 112, an 8-high tower
  for (const [i, a] of dots.entries()) for (const b of dots.slice(i + 1)) expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeGreaterThanOrEqual(13);
  await expect(svg.locator("line.wait")).not.toHaveCount(0); // the real long waits still show
});

/** The fixture feed as today's poller writes it: each 66 westbound bus measured at its next
 *  stop, plus a short-turn bus (another pattern) on the line (lib/waits.ts, lib/project.ts). */
function stopFeed() {
  const line = JSON.parse(readFileSync(new URL("patterns/6665.json", FIX), "utf8"));
  const near = (ft: number) => line.s.reduce((a: number[], s: number[]) => (Math.abs(s[3] - ft) < Math.abs(a[3] - ft) ? s : a));
  const at = near(24000);
  const by8618 = near(29393); // a second short turn level with bus 8618: they stack
  const wb = feed.vehicles.filter((v: { pid: number }) => v.pid === 6665);
  const base = { stop_gap_s: 300, sched_s: 600, sched_due: 0, long_wait: false, together_with: null, together_s: null };
  const by: Record<string, object> = {
    "8185": { stop_gap_s: 1800, last_by: "S1", next_stop: "Chicago & Ashland", sched_due: 3, long_wait: true }, // buses due, none came
    "8118": { stop_gap_s: 5000, last_by: "8185", next_stop: "Chicago & Halsted", sched_due: 1 }, // after a break: not long
    // two buses heading for the same stop: one hole, one claim, one bar
    "8172": { stop_gap_s: 1500, last_by: "8185", next_stop: "Chicago & Racine", next_stpid: "R", sched_due: 2, long_wait: true },
    "8601": { stop_gap_s: 1500, last_by: "8185", next_stop: "Chicago & Racine", next_stpid: "R", sched_due: 2, long_wait: true },
  };
  const shortTurn = { ...wb[0], vid: "S1", pid: 4357, lon: at[0], lat: at[1], pdist: 9000, at_terminal: false,
    ...base, last_by: "8618", next_stop: "Chicago & Damen", next_stpid: "D" };
  const beside = { ...shortTurn, vid: "S2", lon: by8618[0], lat: by8618[1], last_by: "8116", next_stpid: "S2" };
  return { ...feed, vehicles: [
    ...feed.vehicles.filter((v: { pid: number }) => v.pid !== 6665),
    ...wb.map((v: { vid: string }) => ({ ...v, ...base, last_by: null, next_stop: "Chicago & Test", next_stpid: v.vid, ...by[v.vid] })),
    shortTurn,
    beside,
  ] };
}

test("line view: a short-turn bus on the line is drawn hollow, and a long wait runs to it", async ({ page }) => {
  await setUp(page);
  const f = stopFeed();
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/route/66/");
  const svg = page.locator(".sheet svg").filter({ has: page.locator(`[aria-label="Bus 8185"]`) });
  await expect(svg.locator("circle.dot.other")).toHaveCount(2);
  await expect(page.getByText("Runs part of the route")).toBeVisible();
  const cx = async (vid: string) => Number(await svg.locator(`[aria-label="Bus ${vid}"] circle.dot`).getAttribute("cx"));
  const bars = await svg.locator("line.wait").evaluateAll((ls) => ls.map((l) => [Number(l.getAttribute("x1")), Number(l.getAttribute("x2"))]));
  expect(bars).toContainEqual([await cx("8185"), await cx("S1")]); // the stretch no bus is on
  const racine = [await cx("8601"), await cx("8172")];
  expect(bars.filter(([x1]) => racine.includes(x1))).toHaveLength(1); // one hole, one bar
  // Rings and dots never touch: visible edges (a dot's blue inside its 2 px white halo, a
  // ring's outer stroke) keep a gap, however close the buses (user, Sep 28: a ring overlapped)
  const gaps = await page.locator(".sheet svg").evaluateAll((svgs) => svgs.flatMap((svg) => {
    const dots = [...svg.querySelectorAll("circle.dot")].map((c) => {
      const r = Number(c.getAttribute("r")), ring = c.classList.contains("other");
      return { x: Number(c.getAttribute("cx")), y: Number(c.getAttribute("cy")), edge: ring ? r + 1.25 : r - 1 };
    });
    return dots.flatMap((a, i) => dots.slice(i + 1).map((b) => Math.hypot(a.x - b.x, a.y - b.y) - a.edge - b.edge));
  }));
  expect(Math.min(...gaps)).toBeGreaterThanOrEqual(1.5);
});

test("right now: a long wait is named at its stop, and a gap with one bus due isn't one", async ({ page }) => {
  await setUp(page);
  const f = stopFeed();
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  await expect(page.locator(".sheet li").filter({ hasText: "Long wait at Ashland" })).toHaveCount(1);
  await expect(page.locator(".sheet li").filter({ hasText: "Long wait at Halsted" })).toHaveCount(0);
  await expect(page.locator(".sheet li").filter({ hasText: "Long wait at Racine" })).toHaveCount(1);
});

test("bus card: a short turn you tap stays a ring on the same line, highlighted", async ({ page }) => {
  await setUp(page);
  const f = stopFeed();
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/route/66/");
  await page.locator('[aria-label="Bus S1"]').first().click();
  await expect(page.locator(".near")).toContainText("Next stop Chicago & Damen"); // one stop on the card
  await expect(page.getByText("since the last bus passed here")).toBeVisible();
  const line = page.locator(".sheet svg").filter({ has: page.locator('[aria-label="Bus S1"]') });
  await expect(line.locator('[aria-label="Bus S1"] circle.dot.other')).toHaveCount(1); // still a ring
  await expect(line.locator('[aria-label="Bus S1"] circle.ring')).toHaveCount(1); // and the one highlighted
  await expect(line.locator('[aria-label="Bus 8185"] circle.dot.other')).toHaveCount(0); // full route: solid
  await line.locator('[aria-label="Bus S1"]').click(); // tap it again: back to the route
  await expect(page.getByRole("tab", { name: "Now" })).toBeVisible();
  await expect(page.locator(".sheet svg circle.ring")).toHaveCount(0);
});

test("bus card: the wait is measured at the bus's next stop; the back link returns to the route", async ({ page }) => {
  const errors = await setUp(page);
  const f = stopFeed();
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/route/66/");
  await page.locator('[aria-label="Bus 8185"]').first().click();
  await expect(page.locator(".near")).toContainText("Next stop Chicago & Ashland");
  await expect(page.getByText("since the last bus passed here")).toBeVisible();
  await expect(page.locator(".gap:not(.behind) strong")).toHaveText("30 min");
  await page.getByRole("button", { name: "‹ 66 Chicago" }).click();
  await expect(page.getByText("Together", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

for (const rt of ["66", "J14"]) test(`line view: no two pieces of text overlap on #${rt}, at any size`, async ({ page }) => {
  await setUp(page);
  await page.goto(`/route/${rt}/`);
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await tapLineFree(page, page.locator(".sheet svg").first()); // a picked stop adds a text row
  const overlaps = await page.locator(".sheet svg").evaluateAll((svgs) =>
    svgs.flatMap((svg) => {
      const boxes = [...svg.querySelectorAll("text")].map((t) => ({ text: t.textContent, b: (t as SVGTextElement).getBBox() }));
      const hits: string[] = [];
      boxes.forEach((a, i) => boxes.slice(i + 1).forEach((c) => {
        if (a.b.x < c.b.x + c.b.width && c.b.x < a.b.x + a.b.width && a.b.y < c.b.y + c.b.height && c.b.y < a.b.y + a.b.height) {
          hits.push(`${a.text} / ${c.text}`);
        }
      }));
      return hits;
    }));
  expect(overlaps).toEqual([]);
  expect(await page.locator(".sheet .street").count()).toBeGreaterThan(0); // major cross streets shown
});

test("line view: tapping the line names the nearest stop; tapping again clears it", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  const line = page.locator(".sheet svg").first();
  // a stretch of line with no bus on it (a bus there takes the tap: it opened the bus, and its
  // glide kept the spot moving until the click timed out)
  const x = await line.evaluate((svg) => {
    const buses = [...svg.querySelectorAll(".bus .hit")].map((c) => Number(c.getAttribute("cx")));
    return [60, 100, 150, 200, 250, 290].find((x) => buses.every((b) => Math.abs(b - (x + 8)) > 24))!;
  });
  await line.locator(".pickable").click({ position: { x, y: 12 } });
  const name = await line.locator(".picked-name").textContent();
  const stops = JSON.parse(fixture("patterns/6662.json")!.toString()).s.map((s: unknown[]) => s[2]);
  const other = JSON.parse(fixture("patterns/6665.json")!.toString()).s.map((s: unknown[]) => s[2]);
  expect([...stops, ...other]).toContain(name);
  await line.locator(".pickable").click({ position: { x, y: 12 } });
  await expect(line.locator(".picked-name")).toHaveCount(0);
});

test("where: the bus card names a terminal bus's stop and a moving bus's next stop; so does the list", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  await page.locator('[aria-label="Bus 1542"]').first().click(); // at the Austin terminal
  await expect(page.locator(".near")).toContainText("Near Chicago & Austin Terminal");
  await page.getByRole("button", { name: "‹ 66 Chicago" }).click();
  await page.locator('[aria-label="Bus 8185"]').first().click(); // on its way
  await expect(page.locator(".near")).toContainText("Next stop Chicago & Ashland");
  // the home list: every bus given a stop name
  await page.route("**/live/vehicles.json", (r) =>
    r.fulfill({ json: { ...feed, vehicles: feed.vehicles.map((v: object) => ({ ...v, near: "Test & Corner", next_stop: "Test & Corner" })) } }));
  await page.goto("/");
  await expect(page.locator(".sheet li").first()).toContainText(/at (Test & Corner|Test|Corner)/);
});

test("J14's express stretch is marked, and tapping it says there are no stops", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/J14/");
  const line = page.locator(".sheet svg").first();
  await expect(line.locator(".express")).toHaveCount(1);
  await expect(line.locator(".express-label")).toHaveText(/no stops for 7 mi/);
  const run = (await line.locator(".express").boundingBox())!;
  const svgBox = (await line.boundingBox())!;
  // tap the middle of the express run, on the track row, where no bus is
  const hit = async (fx: number) => {
    const x = run.x + run.width * fx, y = run.y + run.height / 2;
    const cls = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.getAttribute("class") ?? "", [x, y]);
    return cls.includes("pickable") || cls.includes("express") || cls.includes("track") ? { x, y } : null;
  };
  let spot = null;
  for (const f of [0.5, 0.4, 0.6, 0.3, 0.7]) if ((spot = await hit(f))) break;
  expect(spot, `a bus-free spot on the express run (svg at ${svgBox.x})`).not.toBeNull();
  await page.mouse.click(spot!.x, spot!.y);
  await expect(line.locator(".picked-name")).toHaveText(/Express: no stops for 7\.\d mi/);
});

test("the top bar doesn't move or change size from first paint to live @both", async ({ page, browser, viewport, isMobile }) => {
  // 1. First paint: the static shell, before any JavaScript (JS off shows exactly that).
  const noJs = await browser.newContext({ javaScriptEnabled: false, viewport: viewport!, isMobile });
  const shellPage = await noJs.newPage();
  await shellPage.goto("/route/66/");
  await expect(shellPage.getByText("Connecting to the live feed…")).toBeVisible();
  const shell = (await shellPage.locator("header.bar").boundingBox())!;
  await noJs.close();

  // 2. The app, still connecting (the feed held back), then 3. live.
  let release: () => void = () => {};
  const held = new Promise<void>((r) => (release = r));
  await setUp(page);
  await page.route("**/live/vehicles.json", async (r) => { await held; await r.fulfill({ json: feed }); });
  await page.goto("/route/66/");
  await expect(page.locator(".shell")).toHaveCount(0); // the app replaced it
  await expect(page.getByText("Connecting to the live feed…")).toBeVisible();
  const connecting = (await page.locator("header.bar").boundingBox())!;
  release();
  await expect(page.locator("header.bar").getByText(/buses on the road/)).toBeVisible();
  const live = (await page.locator("header.bar").boundingBox())!;

  for (const box of [connecting, live]) {
    expect(Math.abs(box.x - shell.x)).toBeLessThan(0.5);
    expect(Math.abs(box.y - shell.y)).toBeLessThan(0.5);
    expect(Math.abs(box.width - shell.width)).toBeLessThan(0.5);
    expect(Math.abs(box.height - shell.height)).toBeLessThan(0.5);
  }
});

test("report card: sums up to 30 days, but the per-day table shows only the latest 3", async ({ page }) => {
  const days = Array.from({ length: 10 }, (_, i) => `2026-09-${String(17 + i).padStart(2, "0")}`); // Sep 17-26
  const row = [100, 90, 4, 1, 5];
  await setUp(page);
  await page.route("**/data/v1/report.json", (r) =>
    r.fulfill({ json: { days, city: days.map(() => row), routes: { "66": days.map(() => row) } } }));
  await page.goto("/route/66/");
  await page.getByRole("tab", { name: "Report Card" }).click();
  await expect(page.getByText("Last 10 days")).toBeVisible();
  await expect(page.getByText("1,000 trips scheduled")).toBeVisible();
  const rows = page.locator(".sheet table tbody tr");
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText("Sep 26"); // newest first
});

test("no on-screen text piece ends with a period (only inner periods between sentences)", async ({ page }) => {
  await setUp(page);
  const ends = async () => page.evaluate(() => {
    const out: string[] = [];
    for (const el of document.querySelectorAll("header.bar *, .sheet *")) {
      const own = [...el.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent ?? "").join("").trim();
      if (own && (el as HTMLElement).offsetParent !== null && /[^.]\.$/.test(own)) out.push(own);
    }
    return out;
  });
  const found: string[] = [];
  await page.goto("/");
  found.push(...(await ends()));
  await page.getByRole("button", { name: "About" }).click();
  found.push(...(await ends()));
  await page.goto("/route/66/");
  found.push(...(await ends()));
  await page.getByRole("tab", { name: "Report Card" }).click();
  found.push(...(await ends()));
  await page.getByRole("tab", { name: "Now" }).click();
  await page.locator(".sheet .bus").first().click();
  found.push(...(await ends()));
  expect(found).toEqual([]);
});

test("bus view: the map shows only the open bus's direction; the route view shows both", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  await busesDrawn(page);
  const dirsOnMap = () => page.evaluate(() => {
    const m = window.map as unknown as { queryRenderedFeatures(o: object): { properties: { rt: string; dir: string } }[] };
    return [...new Set(m.queryRenderedFeatures({ layers: ["bus-dot", "bus-sym"] }).map((f) => f.properties.dir))].sort();
  });
  // Polled: the map redraws a moment after each change (slower on CI runners).
  await expect.poll(async () => (await dirsOnMap()).length, { timeout: 10_000 }).toBe(2); // Eastbound and Westbound
  await page.locator(".sheet .bus").first().click();
  const dir = (await page.locator(".sheet h2").first().innerText()).trim(); // "Eastbound"
  await expect.poll(dirsOnMap, { timeout: 10_000 }).toEqual([dir]);
  await page.getByRole("button", { name: "‹ 66 Chicago" }).click();
  await expect.poll(async () => (await dirsOnMap()).length, { timeout: 10_000 }).toBe(2);
});

test("right now: a bus far ahead isn't the wait after a bunch it saw on an earlier trip (#95, Sep 28)", async ({ page }) => {
  // Two buses bunched at the start of the line; the bus near the far end last saw one of them
  // pass its stop 66 min ago, before that bus turned around. The bunch carries no number (no
  // long wait before it), and the far bus's own long wait is its own claim, at its stop.
  await setUp(page);
  const f = stopFeed();
  const wb = f.vehicles.filter((v: { pid: number }) => v.pid === 6665).sort((a: { pdist: number }, b: { pdist: number }) => a.pdist - b.pdist);
  const [rear, lead] = wb, front = wb.at(-1);
  for (const v of wb) Object.assign(v, { last_by: null, stop_gap_s: 300, sched_due: 0, long_wait: false });
  Object.assign(rear, { last_by: lead.vid, stop_gap_s: 30, together_with: lead.vid, together_s: 30 }); // just behind the lead: together
  Object.assign(front, { at_terminal: false, last_by: lead.vid, stop_gap_s: 3960, sched_s: 1800, sched_due: 3, long_wait: true, next_stop: "Chicago & Kostner", next_stpid: "K" });
  f.vehicles = f.vehicles.filter((v: { vid: string }) => v.vid !== "S1" && v.vid !== "S2");
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  const bunch = page.locator(".sheet li").filter({ hasText: "2 buses together" }).filter({ hasText: "Chicago westbound" });
  await expect(bunch).toHaveCount(1);
  await expect(bunch).not.toContainText("since the last bus");
  await expect(bunch).not.toContainText("66 min");
  const wait = page.locator(".sheet li").filter({ hasText: "Long wait at Kostner" });
  await expect(wait).toContainText("66 min");
  await expect(wait).toContainText("every 30 min");
});

test("bus card: the next bus is where it is, stops back, never a forecast (Sep 29)", async ({ page }) => {
  // Minutes until the next bus were a forecast, wrong by 5+ min 1 time in 10: the card says
  // where the poller found the nearest bus behind that stops here (behind_vid, behind_stops)
  await setUp(page);
  const f = stopFeed();
  const bus = f.vehicles.find((v: { vid: string }) => v.vid === "8185");
  const next = f.vehicles.find((v: { vid: string }) => v.vid === "8118");
  Object.assign(bus, { behind_vid: "8118", behind_stops: 3 });
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/route/66/");
  await page.locator('[aria-label="Bus 8185"]').first().click();
  const behind = page.locator(".gap.behind");
  await expect(behind).toContainText("Next bus");
  await expect(behind).toContainText("3 stops back");
  // the next bus on the left, the wait in front of this one on the right (Oct 3)
  const [b, w] = [await behind.boundingBox(), await page.locator(".gap:not(.behind)").boundingBox()];
  expect(b!.x).toBeLessThan(w!.x);
  expect(Math.abs(b!.y - w!.y)).toBeLessThan(1); // side by side
  await expect(behind).toContainText(`near ${next.near}`);
  // how far back, along the route (same pattern: the difference of their positions), Oct 3
  await expect(behind).toContainText(`${((bus.pdist - next.pdist) / 5280).toFixed(1)} mi back`);
  await expect(page.getByText(/until the next bus/)).toHaveCount(0);
  // heading for the same stop (it can still be miles back on an express stretch: #147 Sep 29)
  Object.assign(bus, { behind_stops: 0 });
  await expect(behind).toContainText("Same next stop", { timeout: 40_000 });
});

test("bus card: a bus with only its last stop left says it's finishing, not \"can't tell\" (Sep 29)", async ({ page }) => {
  await setUp(page);
  const f = stopFeed();
  const bus = f.vehicles.find((v: { vid: string }) => v.vid === "8185");
  Object.assign(bus, { at_terminal: false, next_stop: null, next_stpid: null, stop_gap_s: null, last_by: null, trip: "finishing", behind_vid: "8118", behind_stops: 3 });
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/route/66/");
  await page.locator('[aria-label="Bus 8185"]').first().click();
  await expect(page.locator(".gap").first()).toContainText("Finishing");
  await expect(page.locator(".gap").first()).toContainText("its trip");
  await expect(page.getByText(/can't tell/)).toHaveCount(0);
  await expect(page.locator(".gap.behind")).toHaveCount(0); // nobody waits on a finishing bus's stop
});

test("bus card: the key names short turns, and a ring's colour follows its state (Sep 29)", async ({ page }) => {
  // The bus card's line draws short turns as rings, so its key says what a ring is; a ring is
  // amber when that bus is together with another, blue otherwise; the key's ring is the line's
  // blue (a grey key ring didn't match the line: user, Sep 29)
  await setUp(page);
  const f = stopFeed();
  const s1 = f.vehicles.find((v: { vid: string }) => v.vid === "S1");
  Object.assign(s1, { together_with: "8185", together_s: 40 }); // S1 bunched, S2 not
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/route/66/");
  await page.locator('[aria-label="Bus 8185"]').first().click();
  await expect(page.locator(".sheet .key")).toContainText("Runs part of the route");
  const stroke = (vid: string) =>
    page.locator(`.sheet svg [aria-label="Bus ${vid}"] circle.dot`).first().evaluate((c) => getComputedStyle(c).stroke);
  const [amber, blue, fog] = await page.evaluate(() => {
    const css = getComputedStyle(document.documentElement);
    const rgb = (v: string) => { const el = document.createElement("i"); el.style.color = css.getPropertyValue(v); document.body.append(el); const c = getComputedStyle(el).color; el.remove(); return c; };
    return [rgb("--signal-amber"), rgb("--flag-blue"), rgb("--fog")];
  });
  expect(await stroke("S1")).toBe(amber);
  expect(await stroke("S2")).toBe(blue);
  const keyRing = await page.locator(".sheet .key .dot.other").evaluate((i) => getComputedStyle(i).borderTopColor);
  expect(keyRing).toBe(blue);
  expect(keyRing).not.toBe(fog);
});

test("stop: tap a stop on the map (zoomed in) to see the next buses there, by stops away (Sep 29)", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  // Chicago & Ashland (fixture stops.json, built by transform/site_data.py stops_index)
  await page.evaluate(() => window.map!.jumpTo({ center: [-87.66733, 41.89608], zoom: 17 }));
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["stops"] }).length > 0);
  const pt = await page.evaluate(() => {
    const p = window.map!.project([-87.66733, 41.89608]);
    const r = window.map!.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  });
  await page.mouse.click(pt.x, pt.y);
  await expect(page.locator(".sheet h2")).toHaveText("Chicago & Ashland");
  const row = page.locator(".sheet .rows li").filter({ hasText: "Westbound" });
  await expect(row).toContainText("66");
  await expect(row.locator(".bus").nth(0)).toContainText("1 stop away"); // 8185, just short of it
  await expect(row.locator(".bus").nth(1)).toContainText("12 stops away"); // 8118
  await expect(page.getByText(/min(ute)? until|arrives in/i)).toHaveCount(0); // positions, never a forecast
  await row.locator(".bus").nth(0).click(); // a bus opens its card
  await expect(page.getByText("Bus 8185")).toBeVisible();
  expect(errors).toEqual([]);
});

test("stop: click around like a rider: route, stop, bus, back link, Back/Forward, close (Sep 29)", async ({ page }) => {
  test.slow(); // a long click-through: ~47 s on CI's runner, over the 45 s default (Sep 29)
  const errors = await setUp(page);
  const stops = JSON.parse(fixture("stops.json")!.toString()) as [number, number, string, number[]][];
  // The stops on the map (all of them, not just the ones in view) are exactly those the pids serve
  const drawnStopsServe = async (pids: number[]) => {
    const idx = await page.evaluate(() => (window.map as unknown as { getSource(id: string): { serialize(): {
      data: { features: { properties: { i: number } }[] } } } }).getSource("stops").serialize().data.features.map((f) => f.properties.i));
    const want = stops.flatMap((s, i) => (s[3].some((p) => pids.includes(p)) ? [i] : []));
    return want.length > 0 && want.length < stops.length && JSON.stringify(idx) === JSON.stringify(want);
  };
  const routePids = [...new Set(feed.vehicles.filter((v: { rt: string }) => v.rt === "66").map((v: { pid: number }) => v.pid))] as number[];

  // A route from the search box, zoomed in: only its stops
  await page.goto("/");
  await (await searchBox(page)).fill("66"); // opens after a pause, no Enter
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await busesDrawn(page);
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["route"] }).length > 0);
  await page.evaluate(() => window.map!.jumpTo({ center: [-87.66733, 41.89608], zoom: 16 }));
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["stops"] }).length > 0);
  expect(await drawnStopsServe(routePids)).toBe(true);

  // Tap a stop: its own page in history
  const pt = await page.evaluate(() => {
    const p = window.map!.project([-87.66733, 41.89608]);
    const r = window.map!.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  });
  await page.mouse.click(pt.x, pt.y);
  const title = page.locator(".sheet h2");
  await expect(title).toHaveText("Chicago & Ashland");
  expect(new URL(page.url()).pathname).toBe("/");
  await expect(page.getByLabel("Go to a route")).toHaveValue(""); // left the route it opened

  // The stop's map: no other stop, the stop itself highlighted, only the buses on its card
  type Src = { getSource(id: string): { serialize(): { data: { type: string; features?: { properties: { vid: string } }[] } } } };
  const onMap = () => page.evaluate(() => {
    const m = window.map as unknown as Src;
    return { stops: m.getSource("stops").serialize().data.features!.length, stop: m.getSource("stop").serialize().data.type,
      buses: m.getSource("buses").serialize().data.features!.map((f) => f.properties.vid) };
  });
  const cardBuses = await page.locator(".sheet .bus").count();
  await expect.poll(onMap).toMatchObject({ stops: 0, stop: "Feature" });
  await expect.poll(async () => (await onMap()).buses.length).toBe(cardBuses);
  expect((await onMap()).buses).toContain("8185");

  // A bus from the stop: its back link names the stop, the map shows only that bus's stops
  await page.locator(".sheet .rows li").filter({ hasText: "Westbound" }).locator(".bus").nth(0).click();
  await expect(page.getByText("Bus 8185")).toBeVisible();
  const backLink = page.getByRole("button", { name: /Chicago & Ashland/ });
  await expect(backLink).toBeVisible();
  const pid = feed.vehicles.find((v: { vid: string }) => v.vid === "8185").pid;
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["stops"] }).length > 0);
  expect(await drawnStopsServe([pid])).toBe(true);
  await backLink.click();
  await expect(title).toHaveText("Chicago & Ashland");

  // The same with the browser's Back (a swipe on iPhone), then Back to the route, Forward again
  await page.locator(".sheet .rows li").filter({ hasText: "Westbound" }).locator(".bus").nth(0).click();
  await expect(page.getByText("Bus 8185")).toBeVisible();
  await page.goBack();
  await expect(title).toHaveText("Chicago & Ashland");
  await page.goBack();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await expect(page.getByText("Chicago & Ashland")).toHaveCount(0);
  await page.goForward();
  await expect(title).toHaveText("Chicago & Ashland");

  // Close: back where the rider came from; closing the route then goes to the city, not the stop
  await page.getByRole("button", { name: "Close stop" }).click();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await page.getByRole("button", { name: "Back to all routes" }).click();
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("stop: a stop tapped low on the map stays in view when the sheet rises over it (Sep 29) @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const at: [number, number] = [-87.66733, 41.89608]; // Chicago & Ashland
  // put the stop 70% of the way down: clear of the small sheet, under the half-height one
  await page.evaluate((at) => {
    const m = window.map!, h = m.getCanvas().getBoundingClientRect().height;
    m.jumpTo({ center: at, zoom: 17 });
    (m as unknown as { panBy(o: [number, number], a: object): void }).panBy([0, -(0.7 * h - h / 2)], { duration: 0 });
  }, at);
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["stops"] }).length > 0);
  const where = () => page.evaluate((at) => {
    const p = window.map!.project(at), r = window.map!.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, at);
  const pt = await where();
  await page.mouse.click(pt.x, pt.y);
  await expect(page.locator(".sheet h2")).toHaveText("Chicago & Ashland");
  await expect.poll(async () => {
    const [p, s] = [await where(), await page.locator(".sheet").boundingBox()];
    return p.x < s!.x || p.x > s!.x + s!.width || p.y < s!.y; // not under the sheet
  }).toBe(true);
  expect(errors).toEqual([]);
});

test("stop: the wait shown is this stop's own, never a bus's wait at another stop (Oct 1)", async ({ page }) => {
  // #151 Oct 1 00:20: "no bus for 49 min" at Adams & Canal a minute after a bus passed there.
  // It was the next bus's wait at its own next stop, 73 stops back. Here 8185 (heading for
  // Chicago & Ashland) carries 25 min for its next stop; the stop's own stamp says 48 min.
  const f = { ...feed, vehicles: feed.vehicles.map((v: { vid: string }) => (v.vid === "8185" ? { ...v, long_wait: true, stop_gap_s: 1500 } : v)) };
  const errors = await setUp(page, [], f);
  const waits = { generated_at: f.generated_at, stops: { "66|Westbound": { "622": [2880, 1] } } }; // 622: Chicago & Ashland westbound
  await page.route("**/live/stops.json", (r) => r.fulfill({ json: waits }));
  await page.goto("/");
  await busesDrawn(page);
  const at: [number, number] = [-87.66733, 41.89608]; // Chicago & Ashland
  await page.evaluate((at) => window.map!.jumpTo({ center: at, zoom: 17 }), at);
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["stops"] }).length > 0);
  const pt = await page.evaluate((at) => {
    const p = window.map!.project(at), r = window.map!.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, at);
  await page.mouse.click(pt.x, pt.y);
  const row = page.locator(".rows li").filter({ hasText: "Westbound" });
  await expect(row.getByText("No bus here for 48 min")).toBeVisible();
  await expect(page.locator(".sheet").getByText("25 min")).toHaveCount(0); // the bus's wait, measured elsewhere
  // under the route's name, above its buses: it's about the stop, not the next bus
  const order = () => row.evaluate((li) => {
    const [head, line, next] = [".head", ".last", ".bus"].map((q) => li.querySelector(q)!.getBoundingClientRect());
    return line.top >= head.bottom - 1 && line.bottom <= next.top + 1;
  });
  await expect.poll(order).toBe(true);
  expect(errors).toEqual([]);
});

test("when CTA's feed is down the header says so, not \"0 buses on the road\" (Sep 30)", async ({ page }) => {
  // Sep 30, 12:41-13:01 CDT: CTA's API was unreachable and the site said "0 buses on the road"
  const f = { ...feed, vehicles: [], no_data_since: "2026-09-30T17:40:35+00:00" };
  const errors = await setUp(page, [], f);
  await page.goto("/");
  await expect(page.getByText("No live data from CTA since 12:40 PM")).toBeVisible();
  await expect(page.getByText("buses on the road")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a feed that has stopped updating is shown as not live (Sep 30)", async ({ page }) => {
  // If the poller stops writing (box down), the file stays put: the header must not keep saying
  // "Updated N s ago" with a live dot
  const errors = await setUp(page);
  await page.clock.setFixedTime(new Date(new Date(feed.generated_at).getTime() + 5 * 60_000));
  await page.goto("/");
  await expect(page.getByText("Not updated for 5 min")).toBeVisible();
  await expect(page.locator(".status .dot.stale")).toHaveCount(1);
  expect(errors).toEqual([]);
});

/** The map's camera, to the precision a rider could notice (a few metres, a hundredth of a zoom) */
async function camera(page: Page) {
  await page.waitForTimeout(800); // let any ease finish
  return page.evaluate(() => {
    const m = window.map as unknown as { getCenter(): { lng: number; lat: number }; getZoom(): number };
    return { lng: m.getCenter().lng.toFixed(4), lat: m.getCenter().lat.toFixed(4), zoom: m.getZoom().toFixed(2) };
  });
}

async function tapStop(page: Page, at: [number, number]) {
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["stops"] }).length > 0);
  const pt = await page.evaluate((at) => {
    const p = window.map!.project(at), r = window.map!.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, at);
  await page.mouse.click(pt.x, pt.y);
}

test("back from a stop, by Back or its ×, returns the map to the view the rider left (Oct 3) @both", async ({ page }) => {
  // Zoomed in, a stop tapped, then Back: the map flew out to the whole city
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const at: [number, number] = [-87.66733, 41.89608]; // Chicago & Ashland
  // off-centre, so a re-frame around the stop would show; clear of the phone's sheet
  await page.evaluate((at) => window.map!.jumpTo({ center: [at[0] + 0.0012, at[1] - 0.0006], zoom: 16.4 }), at);
  const before = await camera(page);
  for (const leave of ["back", "x"]) {
    await tapStop(page, at);
    await expect(page.locator(".sheet h2")).toHaveText("Chicago & Ashland");
    if (leave === "back") await page.goBack();
    else await page.getByLabel("Close stop").click();
    await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
    expect(await camera(page)).toEqual(before);
  }
  expect(errors).toEqual([]);
});

for (const leave of ["×", "Back"]) {
  test(`a route's ${leave} returns to the city view it was opened from, not the whole city (Oct 3) @both`, async ({ page }) => {
    const errors = await setUp(page);
    await page.goto("/");
    await busesDrawn(page);
    await page.evaluate(() => window.map!.jumpTo({ center: [-87.70, 41.90], zoom: 13.2 }));
    const before = await camera(page);
    const box = await searchBox(page);
    await box.fill("66");
    await box.press("Enter");
    await expect(page).toHaveURL(/\/route\/66\/$/);
    expect(await camera(page)).not.toEqual(before); // a new route is framed
    if (leave === "×") await page.getByLabel("Back to all routes").click();
    else await page.goBack();
    await expect(page).toHaveURL(/\/$/);
    expect(await camera(page)).toEqual(before);
    expect(errors).toEqual([]);
  });
}

test("Back and Forward between routes return to each route as the rider left it (Oct 3)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const box = await searchBox(page);
  await box.fill("66");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await page.evaluate(() => window.map!.jumpTo({ center: [-87.6668, 41.8961], zoom: 15.3 })); // zoomed into part of #66
  const on66 = await camera(page);
  await box.fill("151");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/route\/151\/$/);
  const on151 = await camera(page);
  await page.goBack();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  expect(await camera(page)).toEqual(on66);
  await page.goForward();
  await expect(page).toHaveURL(/\/route\/151\/$/);
  expect(await camera(page)).toEqual(on151);
});

test("stop: how far along the route the buses are, never when they'll come (Oct 3)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  await page.evaluate(() => window.map!.jumpTo({ center: [-87.66733, 41.89608], zoom: 17 }));
  await tapStop(page, [-87.66733, 41.89608]);
  const row = page.locator(".sheet .rows li").filter({ hasText: "Westbound" });
  await expect(row.locator(".bus").nth(0)).toContainText(/1 stop away\s*(\d+\.\d|under 0\.1) mi/);
  await expect(row.locator(".bus").nth(1)).toContainText(/12 stops away\s*\d+\.\d mi/);
  // the dot between them sits midway (the font's own middle dot has no width: it touched the
  // distance, Oct 3)
  const gaps = await row.locator(".bus").nth(1).locator(".sep").evaluate((dot) => {
    const box = dot.getBoundingClientRect();
    const text = (n: Node, end: boolean) => {
      const r = document.createRange();
      r.selectNodeContents(n);
      const rects = [...r.getClientRects()];
      return end ? rects.at(-1)!.right : rects[0].left;
    };
    // the nearest words on each side ("12 stops away", "1.4 mi")
    const walk = document.createTreeWalker(dot.closest("b")!, NodeFilter.SHOW_TEXT);
    const words: Text[] = [];
    while (walk.nextNode()) if ((walk.currentNode as Text).data.trim()) words.push(walk.currentNode as Text);
    const before = words.filter((n) => dot.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_PRECEDING).at(-1)!;
    const after = words.find((n) => dot.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING)!;
    return { before: box.left - text(before, true), after: text(after, false) - box.right, w: box.width };
  });
  expect(gaps.w).toBeGreaterThan(1);
  expect(Math.abs(gaps.before - gaps.after)).toBeLessThan(1.5);
  await expect(page.getByText(/min(ute)? until|arrives in/i)).toHaveCount(0);
});

test("a stop opened inside a route view: Back returns to the route as the rider left it (Oct 3) @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await busesDrawn(page);
  const at: [number, number] = [-87.66733, 41.89608]; // Chicago & Ashland, on #66
  await page.evaluate((at) => window.map!.jumpTo({ center: [at[0] + 0.0012, at[1] - 0.0006], zoom: 16.2 }), at);
  const before = await camera(page);
  await tapStop(page, at);
  await expect(page.locator(".sheet h2")).toHaveText("Chicago & Ashland");
  await page.goBack();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await expect(page.getByRole("heading", { name: "Chicago", exact: true })).toBeVisible();
  expect(await camera(page)).toEqual(before);
  expect(errors).toEqual([]);
});

test("a route opened by its link: its × leaves the camera where it is (Oct 3) @both", async ({ page }) => {
  // nothing to go back to: an × never jumps somewhere new
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await busesDrawn(page);
  await page.evaluate(() => window.map!.jumpTo({ center: [-87.70, 41.896], zoom: 14.1 }));
  const before = await camera(page);
  await page.getByLabel("Back to all routes").click();
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  expect(await camera(page)).toEqual(before);
  expect(errors).toEqual([]);
});

test("search engines may index every page, under one address each (Oct 4)", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/79/");
  await expect(page.locator('meta[name="robots"]')).toHaveCount(0);
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", "https://chitransit.fyi/route/79/");
  await expect(page.locator('meta[http-equiv="content-security-policy"]')).toHaveCount(1);
  const robots = await (await page.request.get("/robots.txt")).text();
  expect(robots).not.toMatch(/Disallow: \/\s/);
});

test("about: the Unofficial pill is centered on the heading's capitals (Oct 4)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await page.getByRole("button", { name: "About" }).click();
  // Overpass capitals sit ~3 px above the middle of the 20 px heading's line box (4x screenshot).
  // Polled: the sheet may still be growing when About opens.
  const lift = async () => {
    const h2 = (await page.locator(".sheet header h2").boundingBox())!;
    const tag = (await page.locator(".sheet header .tag").boundingBox())!;
    return h2.y + h2.height / 2 - (tag.y + tag.height / 2);
  };
  await expect.poll(lift).toBeGreaterThan(2);
  await expect.poll(lift).toBeLessThan(4);
});

test("home-screen app: the map runs up behind the status bar; no shade in a browser tab (Oct 4)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await expect(page.locator('meta[name="apple-mobile-web-app-status-bar-style"]')).toHaveAttribute("content", "black-translucent");
  await expect(page.locator(".status-scrim")).toBeHidden(); // only in standalone display mode
});

test("installable: a manifest with its icons (Oct 4)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  const href = await page.locator('link[rel="manifest"]').getAttribute("href");
  const m = await (await page.request.get(href!)).json();
  expect(m).toMatchObject({ short_name: "chitransit", start_url: "/", display: "standalone" });
  for (const icon of m.icons) expect((await page.request.get(icon.src)).ok()).toBe(true);
  expect(m.icons.map((i: { sizes: string }) => i.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
});

test("search engines and researchers: schema.org on the home page, security.txt (Oct 4)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  const ld = JSON.parse((await page.locator('script[type="application/ld+json"]').textContent())!);
  expect(ld).toMatchObject({ "@type": "WebApplication", name: "chitransit", isAccessibleForFree: true });
  const sec = await (await page.request.get("/.well-known/security.txt")).text();
  expect(sec).toMatch(/^Contact: /m);
  const expires = new Date(sec.match(/^Expires: (.+)$/m)![1]);
  // RFC 9116: under a year ahead; renew before it lapses
  expect(expires.getTime()).toBeGreaterThan(Date.now());
});

test("link previews: a large image on every page (Oct 4)", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/79/");
  const src = await page.locator('meta[property="og:image"]').getAttribute("content");
  expect(src).toBe("https://chitransit.fyi/og.jpg");
  expect((await page.request.get("/og.jpg")).ok()).toBe(true);
  await expect(page.locator('meta[name="twitter:card"]')).toHaveAttribute("content", "summary_large_image");
});

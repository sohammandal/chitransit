// Every view of the app in a real browser, against the production build with fixed data
// (e2e/fixtures: a saved live feed and nightly files). The basemap still loads from the network.
import { readFileSync, existsSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const FIX = new URL("./fixtures/", import.meta.url);
const feed = JSON.parse(readFileSync(new URL("vehicles.json", FIX), "utf8"));
const fixture = (name: string) => (existsSync(new URL(name, FIX)) ? readFileSync(new URL(name, FIX)) : null);

type Map = { loaded(): boolean; getFilter(id: string): unknown; queryRenderedFeatures(o: object): { properties: { rt: string } }[] };
declare global { interface Window { map?: Map; __e2e?: boolean } }

/** Serve the fixtures (or 404 for the files in `missing`), freeze the clock just after the feed. */
async function setUp(page: Page, missing: string[] = []) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/openfreemap|Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.clock.setFixedTime(new Date(new Date(feed.generated_at).getTime() + 10_000));
  await page.addInitScript((f) => { window.__e2e = true; (window as unknown as { __feed: unknown }).__feed = f; }, feed);
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: feed }));
  await page.route("**/data/v1/**", (r) => {
    const name = new URL(r.request().url()).pathname.replace("/data/v1/", "");
    const body = missing.includes(name) ? null : fixture(name);
    return body ? r.fulfill({ body, contentType: "application/json" }) : r.fulfill({ status: 404 });
  });
  return errors;
}

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

test("city: live count, right-now list, buses on the map", async ({ page }) => {
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

test("route: lines per direction, key, map shows only this route", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await expect(page.getByRole("heading", { name: "Chicago", exact: true })).toBeVisible();
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await expect(page.getByText("Arriving together")).toBeVisible();

  await busesDrawn(page);
  expect(await page.evaluate(() => window.map!.getFilter("bus-dot"))).toEqual(["==", ["get", "rt"], "66"]);
  const shown = await page.evaluate(() => window.map!.queryRenderedFeatures({ layers: ["bus-dot", "bus-sym"] }).map((f) => f.properties.rt));
  expect(new Set(shown)).toEqual(new Set(["66"]));
  await noSideScroll(page);
  expect(errors).toEqual([]);
});

test("bus: tap a bus on the line, see its gaps, go back", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await page.locator(".sheet .bus").first().click();
  await expect(page.getByRole("button", { name: "‹ 66 Chicago" })).toBeVisible();
  await expect(page.getByText(/since the last bus passed|its trip, at the|can't tell when the last bus/)).toBeVisible();
  await page.getByRole("button", { name: "‹ 66 Chicago" }).click();
  await expect(page.getByText("Arriving together")).toBeVisible();
  expect(errors).toEqual([]);
});

test("navigation: list row opens its route; Back returns to the city", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await page.locator(".sheet li button").first().click();
  await expect(page).toHaveURL(/\/route\/[^/]+\/$/);
  await expect(page.getByLabel("Back to all routes")).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
});

test("about: opens and closes", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await page.getByRole("button", { name: "About" }).click();
  await expect(page.getByText(/not affiliated/)).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
});

test("report card: shares add up to 100%, split matches, definitions shown", async ({ page }) => {
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
  await page.goto("/route/66/"); // short in the fixtures: fits in half
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

test("search: typing a route fits the map to its buses", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  await (await searchBox(page)).fill("79");
  await (await searchBox(page)).press("Enter");
  await expect(page).toHaveURL(/\/route\/79\/$/);
  await page.waitForTimeout(1500); // the map eases to the route
  const outside = await page.evaluate(() => {
    const m = window.map as unknown as { getBounds(): { contains(p: [number, number]): boolean } };
    const buses = (window as unknown as { __feed: { vehicles: { rt: string; lon: number; lat: number }[] } }).__feed;
    return buses.vehicles.filter((v) => v.rt === "79" && !m.getBounds().contains([v.lon, v.lat])).length;
  });
  expect(outside).toBe(0);
});

test("my location button is on screen and not covered by the panel", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  await busesDrawn(page);
  const button = page.getByRole("button", { name: "Find my location" });
  await expect(button).toBeVisible();
  const box = (await button.boundingBox())!;
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest("button")?.getAttribute("aria-label"), [box.x + box.width / 2, box.y + box.height / 2]);
  expect(hit).toBe("Find my location");
});

test("map buttons: credits and my-location are the same size, on one edge", async ({ page }) => {
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

test("map fits every route opened in a row: search, close, search again, then a list row", async ({ page }) => {
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
});

test("map fits on route-to-route search, browser Back, and Forward", async ({ page }) => {
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

test("search suggestions float over the map: the top bar keeps its height", async ({ page }) => {
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

test("tapping a bus on the map opens it without moving the map; the back link fits its route", async ({ page }) => {
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

test("tapping a bus on the line zooms the map to it; the back link returns to the whole route", async ({ page }) => {
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

test("line view: no two bus dots overlap (close buses stack)", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  const overlaps = await page.locator(".sheet svg").evaluateAll((svgs) =>
    svgs.flatMap((svg) => {
      const dots = [...svg.querySelectorAll("circle.dot")].map((c) => [Number(c.getAttribute("cx")), Number(c.getAttribute("cy"))]);
      return dots.flatMap((a, i) => dots.slice(i + 1).filter((b) => Math.hypot(a[0] - b[0], a[1] - b[1]) < 13).map(() => 1));
    }));
  expect(overlaps.length).toBe(0);
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
    ...wb.map(([vid, pdist, gap_s, ahead]) => ({ ...base, vid, pdist, gap_s, ahead, at_terminal: false })),
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
  const base = { stop_gap_s: 300, sched_s: 600, sched_due: 0 };
  const by: Record<string, object> = {
    "8185": { stop_gap_s: 1800, last_by: "S1", next_stop: "Chicago & Ashland", sched_due: 3 }, // buses due, none came
    "8118": { stop_gap_s: 5000, last_by: "8185", next_stop: "Chicago & Halsted", sched_due: 1 }, // after a break
    // two buses heading for the same stop: one hole, one claim, one bar
    "8172": { stop_gap_s: 1500, last_by: "8185", next_stop: "Chicago & Racine", next_stpid: "R", sched_due: 2 },
    "8601": { stop_gap_s: 1500, last_by: "8185", next_stop: "Chicago & Racine", next_stpid: "R", sched_due: 2 },
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
  await expect(page.getByText("since the last bus passed Chicago & Damen")).toBeVisible();
  const line = page.locator(".sheet svg").filter({ has: page.locator('[aria-label="Bus S1"]') });
  await expect(line.locator('[aria-label="Bus S1"] circle.dot.other')).toHaveCount(1); // still a ring
  await expect(line.locator('[aria-label="Bus S1"] circle.ring')).toHaveCount(1); // and the one highlighted
  await expect(line.locator('[aria-label="Bus 8185"] circle.dot.other')).toHaveCount(0); // full route: solid
  await line.locator('[aria-label="Bus S1"]').click(); // tap it again: back to the route
  await expect(page.getByRole("tab", { name: "Now" })).toBeVisible();
  await expect(page.locator(".sheet svg circle.ring")).toHaveCount(0);
});

test("bus card: the wait is measured at the bus's next stop", async ({ page }) => {
  await setUp(page);
  const f = stopFeed();
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: f }));
  await page.goto("/route/66/");
  await page.locator('[aria-label="Bus 8185"]').first().click();
  await expect(page.getByText("since the last bus passed Chicago & Ashland")).toBeVisible();
  await expect(page.locator(".gap strong").first()).toHaveText("30 min");
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
  await line.locator(".pickable").click({ position: { x: 150, y: 12 } });
  const name = await line.locator(".picked-name").textContent();
  const stops = JSON.parse(fixture("patterns/6662.json")!.toString()).s.map((s: unknown[]) => s[2]);
  const other = JSON.parse(fixture("patterns/6665.json")!.toString()).s.map((s: unknown[]) => s[2]);
  expect([...stops, ...other]).toContain(name);
  await line.locator(".pickable").click({ position: { x: 150, y: 12 } });
  await expect(line.locator(".picked-name")).toHaveCount(0);
});

test("where: the list and the bus card name the nearest stop", async ({ page }) => {
  await setUp(page);
  await page.goto("/route/66/");
  await page.locator(".sheet .bus").first().click();
  await expect(page.locator(".near")).toContainText(/^\s*Near \S/); // the card names where the bus is
  // the home list: every bus given a stop name
  await page.route("**/live/vehicles.json", (r) =>
    r.fulfill({ json: { ...feed, vehicles: feed.vehicles.map((v: object) => ({ ...v, near: "Test & Corner" })) } }));
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

test("the top bar doesn't move or change size from first paint to live", async ({ page, browser, viewport, isMobile }) => {
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

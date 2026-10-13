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
  querySourceFeatures(id: string): unknown[];
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
  // the poller's pattern files (live/patterns/{pid}.json): the fixtures' live/ ones, else none
  await page.route("**/live/patterns/**", (r) => {
    const body = fixture(new URL(r.request().url()).pathname.slice(1));
    return body ? r.fulfill({ body, contentType: "application/json" }) : r.fulfill({ status: 404 });
  });
  // the train sweep's files (live/trains.json, live/stations.json): the fixtures' live/ ones
  for (const name of ["trains", "stations"]) {
    await page.route(`**/live/${name}.json`, (r) => r.fulfill({ body: fixture(`live/${name}.json`)!, contentType: "application/json" }));
  }
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
  await expect(page.getByText(`${feed.vehicles.length} buses`)).toBeVisible();
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
  await page.getByRole("link", { name: "About" }).click();
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
  await page.getByRole("link", { name: "About" }).click(); // long enough to need "full"
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
  // the map re-frames for the bigger space it now has, then draws its new tiles: the flick below
  // is timed by the page's own timers, which a busy map stretches (a finger's events carry the
  // screen's own time), so it waits for the map to be idle (Oct 11)
  await page.evaluate(() => new Promise<void>((resolve) => {
    const m = window.map as unknown as { once(e: string, f: () => void): void; triggerRepaint(): void };
    m.once("idle", () => resolve());
    m.triggerRepaint(); // idle after the next frame, even if it already was
  }));
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

test("phone: the sheet settling at another height never makes the map jump; a framed route fits again (Oct 11)", async ({ page, isMobile }) => {
  // Oct 11: the map's space changed at once as the sheet settled, and the whole map jumped
  test.skip(!isMobile, "phone layout only");
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const box = await searchBox(page);
  await box.fill("66");
  await box.press("Enter");
  await expect(page.locator(".sheet")).toHaveClass(/half/);
  // its lines drawn: with only a "loading" line the content fits and a tap would skip full size
  await expect(page.locator(".sheet .line svg").first()).toBeVisible();
  await page.locator(".sheet .grab").evaluate((g: HTMLElement) => g.click()); // full: the map keeps half's space
  await expect(page.locator(".sheet")).toHaveClass(/full/);
  await camera(page); // settled
  // a place on the route, every frame, while the sheet steps down to its small size
  const frames = await page.evaluate(async () => {
    const m = window.map! as unknown as Map & { getPadding(): { bottom: number } };
    const at: [number, number] = [-87.6553, 41.8961], out: { pad: number; y: number }[] = [];
    const take = () => out.push({ pad: m.getPadding().bottom, y: m.project(at).y });
    take();
    document.querySelector<HTMLElement>(".sheet .grab")!.click();
    for (const end = performance.now() + 1500; performance.now() < end;) {
      await new Promise(requestAnimationFrame);
      take();
    }
    return out;
  });
  await expect(page.locator(".sheet")).toHaveClass(/peek/);
  const i = frames.findIndex((f) => f.pad !== frames[0].pad);
  expect(i).toBeGreaterThan(0);
  expect(Math.abs(frames[i].y - frames[i - 1].y)).toBeLessThan(10); // the space changed, the map didn't jump
  const moved = frames.at(-1)!.y - frames[0].y, shift = (frames[0].pad - frames.at(-1)!.pad) / 2;
  expect(moved).toBeGreaterThan(shift * 0.6); // then glided down, re-framed in the bigger space
  expect(errors).toEqual([]);
});

test("phone: the title and tabs stay put and drag the sheet; only the rest scrolls", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout only");
  await setUp(page);
  await page.goto("/");
  await page.getByRole("link", { name: "About" }).click(); // long: scrolls at half
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
  await page.getByRole("link", { name: /49B North Western/ }).click();
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
  const row = page.locator(".sheet .row").first();
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
  await expect(bar.getByText(/\d+ buses/)).toBeVisible(); // the app, not the static shell
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
  // every pattern in (live file or nightly's): a late one redraws the line and moves its buses
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await page.waitForLoadState("networkidle");
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
  await expect(page.locator("header.bar").getByText(/\d+ buses/)).toBeVisible();
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
  await page.getByRole("link", { name: "About" }).click();
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
  // (the next sweep's file: a new generated_at, or the app takes it for the old copy again)
  Object.assign(bus, { behind_stops: 0 });
  f.generated_at = new Date(new Date(f.generated_at).getTime() + 30_000).toISOString();
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
  // The stops on the map (all of them, not just the ones in view) are exactly those the pids'
  // patterns serve (riders board at every stop but a pattern's last), corners merged (Oct 10:
  // from the patterns themselves, so a reroute's stops show the minute it starts)
  const corner = (n: string) => n.split(" & ").sort().join(" & ");
  const served = (pid: number) => (JSON.parse(fixture(`patterns/${pid}.json`)!.toString()).s as [number, number, string][]).slice(0, -1).map((s) => corner(s[2]));
  const drawnStopsServe = async (pids: number[]) => {
    const names = await page.evaluate(() => (window.map as unknown as { getSource(id: string): { serialize(): {
      data: { features: { properties: { name: string } }[] } } } }).getSource("stops").serialize().data.features.map((f) => f.properties.name));
    const want = [...new Set(pids.flatMap(served))].sort();
    return names.length > 0 && names.length < stops.length && JSON.stringify([...new Set(names.map(corner))].sort()) === JSON.stringify(want);
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
  await expect(page.getByText(/\d+ buses/)).toHaveCount(0);
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
  await page.getByRole("link", { name: "About" }).click();
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

test("each page's HTML names it in a heading, before any script runs (Oct 5)", async ({ request }) => {
  // search engines read the HTML first; the app draws its own headings later
  const route = await (await request.get("/route/66/")).text();
  expect(route).toMatch(/<h1[^>]*>66 [^<]+ bus, live<\/h1>/);
  const home = await (await request.get("/")).text();
  expect(home).toMatch(/<h1[^>]*>Chicago buses and trains, live<\/h1>/);
});

test("about: outside links open in a new tab, leaving the map where it was (Oct 5)", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await page.getByRole("link", { name: "About" }).click();
  const links = page.locator(".sheet a[href^='http']");
  expect(await links.count()).toBeGreaterThanOrEqual(3);
  for (const a of await links.all()) {
    await expect(a).toHaveAttribute("target", "_blank");
    await expect(a).toHaveAttribute("rel", /noopener/);
  }
});

test("sheet: on a phone the head is held by the sheet under the handle, outside the scrolling list, so nothing scrolls between them (Oct 13)", async ({ page }) => {
  // iPhones (WebKit: Safari and Chrome alike) let a sticky head trail its list while it scrolled:
  // a strip of the list showed between the handle and the title on every sheet (Oct 13)
  await setUp(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Show more" }).click();
  const at = () => page.evaluate(() => {
    const sheet = document.querySelector(".sheet")!, head = sheet.querySelector<HTMLElement>(".body .sheet-head")!;
    const [h, g, first] = [head, sheet.querySelector(".grab")!, head.nextElementSibling!].map((e) => e.getBoundingClientRect());
    return { held: head.offsetParent === sheet && getComputedStyle(head).position === "absolute", gap: h.top - g.bottom, below: first.top - h.bottom };
  });
  const rest = await at();
  expect(rest.held).toBe(true); // not in the list's flow, so the list can't move it
  expect(Math.abs(rest.gap)).toBeLessThan(0.01); // right under the handle
  expect(rest.below).toBeCloseTo(14, 1); // the list starts where it did with the head in its flow
  await page.locator(".sheet .body").evaluate((b) => (b.scrollTop = 200));
  expect(Math.abs((await at()).gap)).toBeLessThan(0.01);
});

test("about: room between the heading and the tagline; a footer like the reports', every old line kept (Oct 12) @both", async ({ page }) => {
  // the tagline sat ~10 px under the heading; the small print was a left-aligned list (Oct 12)
  await setUp(page);
  await page.goto("/");
  await page.getByRole("link", { name: "About" }).click();
  const sheet = page.locator(".sheet");
  const head = (await sheet.getByRole("heading", { name: "Every CTA bus and train, live" }).boundingBox())!;
  const tagline = (await sheet.locator(".what").boundingBox())!;
  expect(tagline.y - (head.y + head.height)).toBeGreaterThanOrEqual(16);
  const foot = sheet.locator("footer.fine");
  await foot.scrollIntoViewIfNeeded();
  await expect(foot.locator(".brand")).toHaveText("chitransit.fyi");
  await expect(foot.getByRole("link")).toHaveText(["Marathon report", "Open source", "Feedback", "transitchicago.com"]);
  for (const line of ["Data provided by Chicago Transit Authority", "not affiliated with, made by, or endorsed by the CTA", "data may be delayed or incomplete", "Privacy: no cookies, accounts or ads"])
    await expect(foot).toContainText(line);
  // centred in the panel (phone and computer alike): the name, the links' row, each line of print
  const off = await foot.evaluate((f) => {
    const sheet = f.closest(".sheet")!, sb = sheet.getBoundingClientRect();
    const mid = sb.left + sheet.clientLeft + sheet.clientWidth / 2;
    const words = (el: Element) => { const r = document.createRange(); r.selectNodeContents(el); const b = r.getBoundingClientRect(); return b.left + b.width / 2; };
    const links = [...f.querySelectorAll("nav a")].map((a) => a.getBoundingClientRect());
    return [words(f.querySelector(".brand")!), (links[0].left + links[links.length - 1].right) / 2, ...[...f.querySelectorAll("p")].map(words)].map((x) => Math.abs(x - mid));
  });
  for (const d of off) expect(d).toBeLessThan(1);
  for (const a of await foot.locator("nav a").all()) {
    const room = await a.evaluate((el) => {
      const r = document.createRange();
      r.selectNodeContents(el);
      const [b, t] = [el.getBoundingClientRect(), r.getBoundingClientRect()];
      return Math.min(t.left - b.left, b.right - t.right);
    });
    expect(room).toBeGreaterThanOrEqual(10);
  }
});

test("about: its own address; Back and × return to what was open under it, bus and all (Oct 11)", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await page.locator(".sheet .bus").first().click();
  const bus = page.getByRole("button", { name: "‹ 66 Chicago" });
  await expect(bus).toBeVisible();
  await page.getByRole("link", { name: "About" }).click();
  await expect(page).toHaveURL(/\/about\/$/);
  await expect(page).toHaveTitle("About | chitransit.fyi");
  await expect(page.getByText(/not affiliated/)).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await expect(bus).toBeVisible(); // the bus About was opened over, still open
  await page.goForward();
  await expect(page.getByText(/not affiliated/)).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await expect(bus).toBeVisible();
  await expect(page).toHaveTitle(/^66 Chicago bus, live/);
  expect(errors).toEqual([]);
});

test("about: a visit that begins at /about/ opens it, its text in the HTML before any script; × goes to the city (Oct 11)", async ({ page, request }) => {
  // search engines and link previews read the HTML; the app shows the same text in its panel
  const html = await (await request.get("/about/")).text();
  expect(html).toMatch(/<h1[^>]*>About chitransit\.fyi<\/h1>/);
  expect(html).toContain("not affiliated with, made by, or endorsed by the CTA");
  expect(await (await request.get("/sitemap-0.xml")).text()).toContain("https://chitransit.fyi/about/");
  const errors = await setUp(page);
  await page.goto("/about/");
  await expect(page.locator(".about-copy")).toHaveCount(0); // the HTML's copy: gone once the panel shows it
  await expect(page.getByText(/not affiliated/)).toBeVisible();
  await expect(page.getByRole("link", { name: "About" })).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "Close" }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/");
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/about\/$/);
  await expect(page.getByText(/not affiliated/)).toBeVisible();
  expect(errors).toEqual([]);
});

test("links: Right now rows, search results and About are real links; a click opens in place (Oct 11) @both", async ({ page }) => {
  // search engines follow only <a href>, and a rider can open one in a new tab
  const errors = await setUp(page);
  await page.goto("/");
  await expect(page.getByRole("link", { name: "About" })).toHaveAttribute("href", "/about/");
  const row = page.locator(".sheet .row").first();
  const href = (await row.getAttribute("href"))!;
  expect(href).toMatch(/^\/route\/[^/]+\/$/);
  await page.evaluate(() => ((window as unknown as { kept: boolean }).kept = true)); // gone if the page loads anew
  await row.click();
  await expect(page).toHaveURL(new RegExp(`${href}$`));
  await expect(page.getByLabel("Back to all routes")).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { kept?: boolean }).kept)).toBe(true);
  await (await searchBox(page)).fill("western");
  await expect(page.getByRole("link", { name: /49B North Western/ })).toHaveAttribute("href", "/route/49B/");
  expect(errors).toEqual([]);
});

/** The feed with #66's Westbound buses on a reroute that only the poller's live files have
 *  (fixtures/live/patterns/966665.json: it skips Chicago & Noble), and CTA's word on it. Oct 10:
 *  CTA started #6's and #146's reroutes after the nightly ran; the site drew none of their buses,
 *  and every #146 stop said "No bus on the way now" as they drove towards it. */
const rerouted = () => ({
  ...feed,
  rerouted: { "66": ["Westbound"] },
  alerts: { "66": [{ h: "#66 Chicago Temporary Westbound Reroute near Chicago/Noble", u: "https://www.transitchicago.com/travel_information/alert_detail.aspx?AlertId=1" }] },
  vehicles: feed.vehicles.map((v: { pid: number }) => (v.pid === 6665 ? { ...v, pid: 966665 } : v)),
});

/** Click the stop drawn at [lon, lat] (zoomed in to street level). */
async function openStopAt(page: Page, at: [number, number]) {
  await page.evaluate((at) => window.map!.jumpTo({ center: at, zoom: 17 }), at);
  await page.waitForFunction(() => window.map!.queryRenderedFeatures({ layers: ["stops"] }).length > 0);
  const pt = await page.evaluate((at) => {
    const p = window.map!.project(at);
    const r = window.map!.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, at);
  await page.mouse.click(pt.x, pt.y);
}

test("a reroute's own pattern (live files): its buses on the line, the streets it skips dashed, CTA's alert (Oct 10) @both", async ({ page }) => {
  const errors = await setUp(page, [], rerouted());
  await page.goto("/route/66/");
  await expect(page.locator(".sheet svg")).toHaveCount(2); // both directions drawn
  await expect(page.locator(".lab", { hasText: "Westbound" }).locator(".tag")).toHaveText("Rerouted");
  await expect(page.locator(".lab", { hasText: "Eastbound" }).locator(".tag")).toHaveCount(0);
  const alert = page.locator(".alerts a");
  await expect(alert).toHaveText("#66 Chicago Temporary Westbound Reroute near Chicago/Noble");
  await expect(alert).toHaveAttribute("target", "_blank");
  await busesDrawn(page);
  await page.waitForFunction(() => window.map!.querySourceFeatures("regular").length > 0);
  await noSideScroll(page);
  expect(errors).toEqual([]);
});

test("alerts: only a link to CTA's own site is a link; any other shows as plain text (Oct 11)", async ({ page }) => {
  // the feed passes CTA's alert links through: a tampered feed (CTA's or our box's) can't plant one
  const f = { ...rerouted(), alerts: { "66": [
    { h: "#66 Chicago Temporary Westbound Reroute near Chicago/Noble", u: "https://www.transitchicago.com/travel_information/alert_detail.aspx?AlertId=1" },
    { h: "Planted headline", u: "https://example.net/login" },
  ] } };
  const errors = await setUp(page, [], f);
  await page.goto("/route/66/");
  await expect(page.locator(".alerts li")).toHaveCount(2);
  await expect(page.locator(".alerts a")).toHaveCount(1);
  await expect(page.locator(".alerts a")).toHaveAttribute("href", /^https:\/\/www\.transitchicago\.com\//);
  await expect(page.locator(".alerts li", { hasText: "Planted headline" }).locator("a")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("stop: buses on a reroute's pattern count, and a stop it skips says so, never 'no bus' (Oct 10)", async ({ page }) => {
  const errors = await setUp(page, [], rerouted());
  await page.goto("/");
  await busesDrawn(page);
  await openStopAt(page, [-87.66733, 41.89608]); // Chicago & Ashland: the reroute stops there
  await expect(page.locator(".sheet h2")).toHaveText("Chicago & Ashland");
  const row = page.locator(".sheet .rows li").filter({ hasText: "Westbound" });
  await expect(row).toContainText("66");
  await expect(row.locator(".bus").nth(0)).toContainText("1 stop away"); // 8185, on the reroute
  await page.evaluate(() => history.replaceState(null, "", "/")); // a reload keeps an open stop
  await page.goto("/");
  await busesDrawn(page);
  await openStopAt(page, [-87.66236, 41.89616]); // Chicago & Noble: the reroute skips it
  await expect(page.locator(".sheet h2")).toHaveText("Chicago & Noble");
  await expect(page.getByText("CTA's reroute skips this stop: 66 Westbound")).toBeVisible();
  await expect(page.getByText(/No bus on the way now:.*66 Westbound/)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the map draws only the patterns buses are on now: one whose last bus left goes (Oct 10)", async ({ page }) => {
  // #6, 20:02-20:10: one southbound bus on the regular pattern (7107) during the reroute; after it
  // left, its line stayed drawn under the reroute's dashes (the page keeps every pattern it loaded)
  const errors = await setUp(page);
  let current = feed;
  await page.route("**/live/vehicles.json", (r) => r.fulfill({ json: current })); // newer than setUp's: wins
  await page.goto("/route/66/");
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await busesDrawn(page);
  const lines = () => page.evaluate(() => {
    const src = (window.map as unknown as { getSource(id: string): { serialize(): { data: { geometry?: { coordinates: unknown[] } } } } | undefined })
      .getSource("route");
    return src?.serialize().data.geometry?.coordinates.length ?? 0;
  });
  await expect.poll(lines).toBe(2); // Eastbound and Westbound
  current = {
    ...feed,
    generated_at: new Date(new Date(feed.generated_at).getTime() + 30_000).toISOString(),
    vehicles: feed.vehicles.filter((v: { rt: string; dir: string }) => !(v.rt === "66" && v.dir === "Westbound")),
  };
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange"))); // fetch the feed now
  await expect.poll(lines).toBe(1);
  expect(errors).toEqual([]);
});

test("route: the Rerouted pill is centred on its direction's capitals, and its text in it (Oct 10)", async ({ page }) => {
  await setUp(page, [], rerouted());
  await page.goto("/route/66/");
  const tag = page.locator(".lab .tag");
  await expect(tag).toHaveText("Rerouted");
  // Measured at 4x: the pill 0.00 px off the label's capitals, its text 0.12 px off its own centre.
  // In box terms: the label text's box centre sits 1.5 px below the pill's (0.5 when it sat ~1 px low),
  // and Overpass capitals ride high, so the pill's text has 2 px more room above than below.
  const lift = await page.locator(".lab", { has: page.locator(".tag") }).evaluate((el) => {
    const r = document.createRange();
    r.selectNodeContents(el.querySelector("b")!.firstChild!);
    const t = r.getBoundingClientRect(), p = el.querySelector(".tag")!.getBoundingClientRect();
    return (t.top + t.bottom) / 2 - (p.top + p.bottom) / 2;
  });
  expect(lift).toBeGreaterThan(1.2);
  expect(lift).toBeLessThan(1.8);
  expect(await tag.evaluate((e) => parseFloat(getComputedStyle(e).paddingTop) - parseFloat(getComputedStyle(e).paddingBottom))).toBe(2);
});

test("right now: a long wait or bunch on a route CTA has rerouted says so, the wait still shown (Oct 11) @both", async ({ page }) => {
  // On reroute days (Marathon, Oct 11) detoured routes' long waits are real (GPS-checked Oct 10),
  // but read as CTA failures without the context
  await setUp(page);
  await page.goto("/");
  const first = page.locator(".row").first();
  const rt = (await first.locator(".rt").textContent())!.trim();
  const dir = (await first.locator(".what span").textContent())!.trim().split(" ").pop()!; // "westbound"
  const Dir = dir[0].toUpperCase() + dir.slice(1);
  await setUp(page, [], { ...feed, rerouted: { [rt]: [Dir] } });
  await page.goto("/");
  const rows = page.locator(".row");
  await expect(rows.first().locator(".tag")).toHaveText("Rerouted");
  await expect(rows.first().locator(".rt")).toHaveText(rt);
  await expect(rows.first().locator(".what b")).not.toBeEmpty(); // the claim itself, unchanged
  const tagged = await rows.evaluateAll((els) => els.filter((e) => e.querySelector(".tag"))
    .map((e) => `${e.querySelector(".rt")!.textContent!.trim()} ${e.querySelector(".what span")!.textContent!.toLowerCase()}`));
  for (const t of tagged) expect(t.startsWith(`${rt} `) && t.includes(dir)).toBe(true); // only that route and direction
  await noSideScroll(page);
});


// ---------- the 'L' (Oct 11): CTA's own data, display only ----------

const trainFixture = JSON.parse(readFileSync(new URL("live/trains.json", FIX), "utf8"));
const trainsDrawn = (page: Page) => page.evaluate(() => window.map!.queryRenderedFeatures({ layers: ["train-dot"] }).length);
const busesOnMap = (page: Page) => page.evaluate(() => window.map!.queryRenderedFeatures({ layers: ["bus-dot", "bus-sym"] }).length);

test("trains: two at one spot stand apart, one each way either side of it, one behind the other the same way (Oct 12) @both", async ({ page }) => {
  // CTA gives every train at a station the station's point: two Red Line trains, one each way,
  // drew as one square with a star of two arrows (Oct 12). The fixture has both kinds of pair
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  await expect.poll(() => trainsDrawn(page), { timeout: 15_000 }).toBeGreaterThan(20);
  const drawnAt = (lon: number, lat: number) => page.evaluate(async ([lon, lat]) => {
    type Spot = { x: number; y: number };
    const m = (window as unknown as { map: { jumpTo(o: object): void; once(e: string, f: (v?: unknown) => void): void; project(p: [number, number]): Spot;
      queryRenderedFeatures(p: [number, number], o: object): { properties: { key: string } }[] } }).map;
    m.jumpTo({ center: [lon, lat], zoom: 16 });
    await new Promise((r) => m.once("idle", r));
    const c = m.project([lon, lat]);
    const at = (dx: number, dy: number) => m.queryRenderedFeatures([c.x + dx, c.y + dy], { layers: ["train-dot"] }).map((f) => f.properties.key).sort();
    return { above: at(0, -16), below: at(0, 16) };
  }, [lon, lat]);
  // westbound 817 to the right of its way (north, above), eastbound 841 to the right of its (below)
  expect(await drawnAt(-87.65493, 41.95377)).toEqual({ above: ["red:817"], below: ["red:841"] });
  // Green and Orange northbound on their shared track: one ahead of the other
  expect(await drawnAt(-87.62659, 41.86744)).toEqual({ above: ["org:717"], below: ["g:012"] });
  expect(errors).toEqual([]);
});

test("trains: on the map in their line's colour; the switch shows buses, trains or both, remembered (Oct 11) @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  await expect.poll(() => trainsDrawn(page), { timeout: 15_000 }).toBeGreaterThan(20);
  const show = page.getByRole("radiogroup", { name: "Show" });
  await expect(show.getByRole("radio", { name: "All" })).toHaveAttribute("aria-checked", "true");

  await show.getByRole("radio", { name: "Train" }).click();
  await expect.poll(() => busesOnMap(page)).toBe(0);
  expect(await trainsDrawn(page)).toBeGreaterThan(20);
  await expect(page.getByText(`${trainFixture.trains.length} trains`).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "The 'L'" })).toBeVisible(); // the lines, not Right now
  await expect(page.locator(".sheet .row")).toHaveCount(8);

  await page.reload(); // remembered on this device
  await expect(page.getByRole("radio", { name: "Train" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("radio", { name: "Bus" }).click();
  await busesDrawn(page);
  await expect.poll(() => trainsDrawn(page)).toBe(0);
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  await page.getByRole("radio", { name: "All" }).click();
  await expect.poll(() => trainsDrawn(page)).toBeGreaterThan(20);
  expect(errors).toEqual([]);
});

test("line: search finds the Red Line; /line/red/ shows CTA's status and its trains by direction; Back returns (Oct 11)", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await (await searchBox(page)).fill("red");
  const link = page.getByRole("link", { name: /Red Line/ });
  await expect(link).toHaveAttribute("href", "/line/red/");
  await link.click();
  await expect(page).toHaveURL(/\/line\/red\/$/);
  await expect(page.getByRole("heading", { name: "Red Line" })).toBeVisible();
  const red = trainFixture.trains.filter((t: { rt: string }) => t.rt === "red");
  await expect(page.locator(".sheet .sub")).toHaveText(`${red.length} trains`);
  await expect(page.getByText(`CTA: ${trainFixture.status.red}`)).toBeVisible();
  await expect(page.getByText("Toward Howard")).toBeVisible();
  // each direction a strip, as a bus route's: its trains on it, stations named along it
  const strip = page.locator(".sheet section svg").first();
  await expect(strip.locator(".train")).not.toHaveCount(0);
  expect(await strip.locator("text.name").count()).toBeGreaterThanOrEqual(3);
  await expect.poll(async () => new Set(await page.evaluate(() =>
    window.map!.queryRenderedFeatures({ layers: ["train-dot"] }).map((f) => f.properties.rt)))).toEqual(new Set(["red"]));
  await page.goBack();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/");
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("train and station: a train's next station opens CTA's times there, by platform (Oct 11)", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/line/red/");
  await page.locator(".sheet .train").first().click();
  await expect(page.getByRole("button", { name: /‹ Red Line/ })).toBeVisible();
  await page.locator(".sheet .next").click();
  await expect(page.locator(".sheet h2")).not.toHaveText("Red Line");
  await expect(page.locator(".sheet .lab").first()).toBeVisible(); // a platform ("Service toward Howard")
  await expect(page.locator(".sheet li .when").first()).toHaveText(/^(Due|\d+ min|Delayed)$/);
  await page.getByRole("button", { name: "Close station" }).click(); // Back to the line
  await expect(page).toHaveURL(/\/line\/red\/$/);
  expect(errors).toEqual([]);
});

const stations = JSON.parse(readFileSync(new URL("../public/l/lines.json", import.meta.url), "utf8")).stations;

/** Whether a station shows on the map: on the canvas, clear of the top bar and the sheet. */
async function stationShown(page: Page, id: string) {
  await page.waitForTimeout(800); // let any ease finish
  return page.evaluate((at) => {
    const p = window.map!.project(at), r = window.map!.getCanvas().getBoundingClientRect();
    const x = r.left + p.x, y = r.top + p.y;
    const under = (sel: string) => {
      const b = document.querySelector(sel)?.getBoundingClientRect();
      return !!b && x >= b.left && x <= b.right && y >= b.top && y <= b.bottom;
    };
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom && !under(".bar") && !under(".sheet");
  }, [stations[id].lon, stations[id].lat] as [number, number]);
}

test("trains: Back and Forward through a line, a train and its next station return each view as it was (Oct 11) @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const city = await camera(page);
  await (await searchBox(page)).fill("red");
  await page.getByRole("link", { name: /Red Line/ }).click();
  await expect(page).toHaveURL(/\/line\/red\/$/);
  const red = await camera(page);
  expect(red).not.toEqual(city);
  await page.locator(".sheet .train").first().click(); // a train on the line's strip
  await expect(page.getByRole("button", { name: /‹ Red Line/ })).toBeVisible();
  const onTrain = await camera(page);
  expect(Number(onTrain.zoom)).toBeGreaterThanOrEqual(15); // the map on it, as a bus tapped on its line
  const next = await page.locator(".sheet .next b").innerText();
  await page.locator(".sheet .next").click();
  await expect(page.locator(".sheet h2")).toHaveText(next);
  const id = await page.evaluate(() => history.state.station as string);
  expect(await stationShown(page, id)).toBe(true);

  await page.goBack(); // the train it was opened from, the map on it again
  await expect(page).toHaveURL(/\/line\/red\/$/);
  await expect(page.getByRole("button", { name: /‹ Red Line/ })).toBeVisible();
  expect(await camera(page)).toEqual(onTrain);
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  expect(await camera(page)).toEqual(city);

  await page.goForward();
  await expect(page.getByRole("button", { name: /‹ Red Line/ })).toBeVisible();
  expect(await camera(page)).toEqual(onTrain);
  await page.goForward();
  await expect(page.locator(".sheet h2")).toHaveText(next);
  expect(await stationShown(page, id)).toBe(true);

  await page.getByRole("button", { name: "Close station" }).click(); // the train again
  await page.getByRole("button", { name: /‹ Red Line/ }).click(); // closed in place: it stays closed
  await page.goBack();
  await page.goForward();
  await expect(page.getByRole("heading", { name: "Red Line" })).toBeVisible();
  await expect(page.getByRole("button", { name: /‹ Red Line/ })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("an open line shows only trains; an open route only buses (Oct 11) @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/line/red/");
  await expect(page.getByText(/\d+ buses/)).toBeVisible(); // the bus feed is in
  await page.waitForFunction(() => !!window.map?.loaded());
  await expect.poll(() => trainsDrawn(page), { timeout: 15_000 }).toBeGreaterThan(0);
  expect(await busesOnMap(page)).toBe(0);
  await page.getByLabel("Back to the map").click();
  await busesDrawn(page);
  const box = await searchBox(page);
  await box.fill("66");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/route\/66\/$/);
  await expect.poll(() => trainsDrawn(page)).toBe(0);
  expect(await busesOnMap(page)).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test("a train's back link glides back to its whole line; the line's × to the view it was opened from (Oct 11) @both", async ({ page }) => {
  // Oct 11: back from a train to its line stayed zoomed on the train; × from a line opened from
  // the untouched city stayed on the line
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const city = await camera(page);
  await (await searchBox(page)).fill("red");
  await page.getByRole("link", { name: /Red Line/ }).click();
  await expect(page).toHaveURL(/\/line\/red\/$/);
  const red = await camera(page);
  await page.locator(".sheet .train").first().click();
  expect(Number((await camera(page)).zoom)).toBeGreaterThanOrEqual(15);
  await page.getByRole("button", { name: /‹ Red Line/ }).click();
  await expect(page.getByRole("heading", { name: "Red Line" })).toBeVisible();
  expect(await camera(page)).toEqual(red); // the whole line again
  await page.getByLabel("Back to the map").click();
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();
  expect(await camera(page)).toEqual(city); // where the map was before the line
  expect(errors).toEqual([]);
});

test("a station opened while its line is still being framed comes into view, and stays after a reload (Oct 11) @both", async ({ page }) => {
  // the station's move cancelled the line's fit and was then cancelled itself; a reload framed the city
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  await (await searchBox(page)).fill("red");
  await page.getByRole("link", { name: /Red Line/ }).click();
  // a train and its next station at once, before the fit ends (a click() would wait for the sheet)
  await page.evaluate(async () => {
    document.querySelector(".sheet .train")!.dispatchEvent(new MouseEvent("click", { bubbles: true })); // an SVG strip's train
    await new Promise((r) => setTimeout(r));
    document.querySelector<HTMLElement>(".sheet .next")!.click();
  });
  const id = await page.evaluate(() => history.state.station as string);
  expect(await stationShown(page, id)).toBe(true);
  await page.reload();
  await expect(page.locator(".sheet h2")).toHaveText(stations[id].name);
  expect(await stationShown(page, id)).toBe(true);
  expect(errors).toEqual([]);
});

test("a station's name opens it too: a train at the station covers its circle (Oct 11) @both", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await page.getByRole("radio", { name: "Train" }).click(); // no buses or stops under the finger
  await page.waitForFunction(() => !!window.map?.loaded());
  const id = "40380"; // Clark/Lake
  await page.evaluate((at) => window.map!.jumpTo({ center: at, zoom: 15 }), [stations[id].lon, stations[id].lat] as [number, number]);
  // a spot on its name, clear of the circle and of any train
  const spot = await page.waitForFunction((at) => {
    const m = window.map! as unknown as Map & { queryRenderedFeatures(p: [number, number], o: object): { layer: { id: string } }[] };
    const c = m.project(at), r = m.getCanvas().getBoundingClientRect();
    for (let dx = -120; dx <= 120; dx += 4) for (let dy = -24; dy <= 24; dy += 4) {
      const hits = m.queryRenderedFeatures([c.x + dx, c.y + dy], { layers: ["l-names", "l-stations-hit", "train-dot"] });
      if (hits.length && hits.every((h) => h.layer.id === "l-names")) return { x: r.left + c.x + dx, y: r.top + c.y + dy };
    }
    return null;
  }, [stations[id].lon, stations[id].lat] as [number, number]);
  const { x, y } = (await spot.jsonValue())!;
  await page.mouse.click(x, y);
  await expect(page.locator(".sheet h2")).toHaveText("Clark/Lake");
  expect(errors).toEqual([]);
});

test("no train feed yet: a line says Loading…, never that CTA shows no trains (Oct 11)", async ({ page }) => {
  const errors = await setUp(page);
  await page.route("**/live/trains.json", (r) => r.fulfill({ status: 404 })); // over setUp's
  await page.goto("/line/red/");
  await expect(page.getByRole("heading", { name: "Red Line" })).toBeVisible();
  await expect(page.locator(".sheet .sub")).toHaveText("Loading…");
  await expect(page.getByText(/no trains|0 trains/)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a view opened from a scrolled list starts at its top (Oct 11)", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  const body = page.locator(".sheet .body");
  await body.evaluate((b) => (b.scrollTop = b.scrollHeight));
  await expect.poll(() => body.evaluate((b) => b.scrollTop)).toBeGreaterThan(100);
  await page.getByRole("link", { name: "About" }).click();
  await expect(page).toHaveURL(/\/about\/$/);
  expect(await body.evaluate((b) => b.scrollHeight > b.clientHeight)).toBe(true); // it can scroll
  expect(await body.evaluate((b) => b.scrollTop)).toBe(0);
  expect(errors).toEqual([]);
});

test("a line's strip marks a long wait in red and trains together in amber, as a route's does (Oct 11) @both", async ({ page }) => {
  const errors = await setUp(page); // the fixture: 820 in a 25 min wait; 817 together with 925
  await page.goto("/line/red/");
  const toHoward = page.locator(".sheet section").filter({ hasText: "Toward Howard" }).locator("svg");
  await expect(toHoward.locator("line.wait")).toHaveCount(1);
  await expect(toHoward.locator("text.min.long")).toHaveText("25 min");
  await expect(toHoward.locator("rect.sq.together")).toHaveCount(2);
  await expect(toHoward.locator("text.min.together")).toHaveText("1 min"); // ours (70 s), not CTA's
  // the other gaps: the poller's minutes to the train ahead (CTA's times), in plain ink
  await expect(toHoward.locator("text.min:not(.long):not(.together)")).toHaveText(["9 min", "13 min", "7 min"]);
  await expect(page.locator(".sheet .key")).toContainText("Together");
  await expect(page.locator(".sheet section").filter({ hasText: "Toward 95th" }).locator("line.wait")).toHaveCount(0);
  // a line with no claims shows no key for them (claims go out only once graded)
  await page.goto("/line/blue/");
  await expect(page.getByRole("heading", { name: "Blue Line" })).toBeVisible();
  await expect(page.locator(".sheet .key")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("Purple's trains toward Howard run on a strip from Linden, not against the Express path's (Oct 12)", async ({ page }) => {
  // 511 (the fixture), southbound on the Linden-Howard shuttle: its strip ran Clark/Lake -> Linden
  const errors = await setUp(page);
  await page.goto("/line/purple/");
  const toHoward = page.locator(".sheet section").filter({ hasText: "Toward Howard" }).locator("svg").first();
  await expect(toHoward).toHaveAttribute("aria-label", "1 train toward Howard, from Linden");
  await expect(toHoward.locator(".train")).toHaveCount(1);
  await expect(toHoward.locator("text.end").first()).toHaveText("Linden");
  expect(errors).toEqual([]);
});

test("a station's tracked times are marked live; a timetable time is grey and unmarked (Oct 11)", async ({ page }) => {
  // the eye caught "9 min" first and could miss its "Scheduled" tag (rider, Oct 11)
  const errors = await setUp(page);
  await page.goto("/");
  await page.evaluate(() => history.replaceState({ station: "40010", prev: null }, "", "/"));
  await page.reload(); // a reload keeps an open station
  await expect(page.locator(".sheet h2")).toHaveText(stations["40010"].name);
  await expect(page.locator(".sheet .src .live")).toHaveCount(1); // the legend: CTA's live times
  const rows = page.locator(".sheet li");
  await expect(rows.first()).toBeVisible();
  const kinds = new Set<boolean>();
  for (let i = 0; i < (await rows.count()); i++) {
    const row = rows.nth(i), sch = (await row.locator(".tag", { hasText: "Scheduled" }).count()) > 0;
    kinds.add(sch);
    await expect(row.locator(".live")).toHaveCount(sch ? 0 : 1);
    expect(await row.locator(".when").evaluate((e) => e.classList.contains("sch"))).toBe(sch);
  }
  expect(kinds).toEqual(new Set([true, false])); // both kinds on this board
  // where the rider's eye put the mark (Oct 11; it sat 3.8 px low, then high): on a time, a pixel
  // under the middle of the lowercase "min"; in the legend, on the middle of the capitals "CTA"
  const offsets = await page.locator(".sheet .when:has(.live), .sheet .src").evaluateAll((els) => els.map((el) => {
    const svg = el.querySelector<SVGSVGElement>(".live")!, box = svg.getBoundingClientRect();
    let y0 = Infinity, y1 = -Infinity;
    for (const p of svg.querySelectorAll<SVGPathElement>("path")) {
      const b = p.getBBox();
      [y0, y1] = [Math.min(y0, b.y - 0.75), Math.max(y1, b.y + b.height + 0.75)]; // with the stroke
    }
    const ink = box.top + ((y0 + y1) / 2) * (box.height / svg.viewBox.baseVal.height);
    const text = el.matches(".src") ? el.querySelector("b")! : el;
    const probe = document.createElement("span");
    probe.style.cssText = "display:inline-block;width:0;height:0;vertical-align:baseline";
    text.appendChild(probe);
    const base = probe.getBoundingClientRect().top;
    probe.remove();
    const cs = getComputedStyle(text), ctx = document.createElement("canvas").getContext("2d")!;
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    return el.matches(".src")
      ? Math.abs(ink - (base - ctx.measureText("CTA").actualBoundingBoxAscent / 2))
      : Math.abs(ink - (base - ctx.measureText("x").actualBoundingBoxAscent / 2) - 1);
  }));
  expect(offsets.length).toBeGreaterThan(1);
  expect(Math.max(...offsets)).toBeLessThan(1);
  expect(errors).toEqual([]);
});

test("each line has a page whose HTML names it, before any script runs (Oct 11)", async ({ request }) => {
  const html = await (await request.get("/line/blue/")).text();
  expect(html).toMatch(/<h1[^>]*>Blue Line trains, live<\/h1>/);
  // riders' names in addresses, not CTA's codes
  expect(await (await request.get("/line/brown/")).text()).toMatch(/<h1[^>]*>Brown Line trains, live<\/h1>/);
  expect((await request.get("/l/lines.json")).ok()).toBe(true);
});


// ---------- the Marathon retro (Oct 12): a data story on its own page ----------

const REPORT = "/reports/marathon-2026/";

/** The report's data files (published apart from the site): the fixtures' reports/ ones. */
async function reportData(page: Page) {
  await page.route("**/data/reports/**", (r) => {
    const body = fixture(new URL(r.request().url()).pathname.replace("/data/", ""));
    return body ? r.fulfill({ body, contentType: "application/json" }) : r.fulfill({ status: 404 });
  });
}

test("report: the Marathon retro's text is in the HTML, with a link preview of its own and a place in the sitemap (Oct 12) @both", async ({ request }) => {
  const html = await (await request.get(REPORT)).text();
  expect(html).toContain("How CTA buses ran on Marathon Sunday</h1>");
  expect(html).toMatch(/stops were in a gap of 90 minutes or more between their usual buses/);
  expect(html).toContain("not affiliated with");
  // a number written into the text keeps its spaces: Astro drops a line break next to an expression
  // ("came at5:47 PM", "692stops", "the last#10", Oct 12)
  const text = html.slice(html.indexOf("<article"), html.indexOf("</article>")).replace(/<script[\s\S]*?<\/script>/g, "").replace(/<[^>]+>/g, " ");
  expect(text).not.toMatch(/[a-z][0-9]|[0-9][a-z]{3,}|[a-z]#[0-9]/);
  expect(html).toContain('property="og:type" content="article"');
  expect(html).toContain('content="https://chitransit.fyi/reports/marathon-2026/og.jpg"');
  expect(await (await request.get("/sitemap-0.xml")).text()).toContain("https://chitransit.fyi/reports/marathon-2026/");
  // the app's pages keep the app's preview
  expect(await (await request.get("/")).text()).toContain('content="https://chitransit.fyi/og.jpg"');
});

test("report: the timelapse opens at a linked moment, still; charts and the reroutes map draw; no side scroll (Oct 12) @both", async ({ page }) => {
  const errors = await setUp(page);
  await reportData(page);
  await page.goto(`${REPORT}?at=12:40`);
  const readout = page.locator(".timelapse .readout");
  await expect(readout.locator("strong")).toHaveText("12:40 PM");
  await expect(readout).toContainText(/\d+ stops without their buses/);
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible(); // a linked moment doesn't play
  await expect(page.getByRole("img", { name: /buses out stayed near 100% of a normal Sunday/ })).toBeVisible();
  await expect(page.getByRole("img", { name: /Buses crossing the Marathon course every 15 minutes/ })).toBeVisible();
  await expect(page.getByRole("img", { name: /34 rerouted routes. CTA's race-day alerts said 6:30 AM to 4 PM/ })).toBeVisible();
  await page.locator(".reroutes").scrollIntoViewIfNeeded(); // its map is made when scrolled near
  await page.getByRole("combobox", { name: "Your route", exact: true }).click();
  await page.getByRole("option", { name: "#22 Clark", exact: true }).click();
  await expect(page.locator("#route-explorer .cut")).toHaveText("63 of its 152 stops cut off");
  // the card leads to the route's live page
  await expect(page.getByRole("link", { name: "See the #22 live now" })).toHaveAttribute("href", "/route/22/");
  await noSideScroll(page);
  expect(errors).toEqual([]);
});

test("report: dragging the timelapse's slider moves the clock and the count (Oct 12)", async ({ page }) => {
  await setUp(page);
  await reportData(page);
  await page.goto(`${REPORT}?at=6:00`);
  const readout = page.locator(".timelapse .readout strong");
  await expect(readout).toHaveText("6 AM");
  await page.getByLabel("Time of day").fill("46"); // 5 AM + 46 x 10 min
  await expect(readout).toHaveText("12:40 PM");
});

test("report: \"How we measured this\" shows it can be opened, and opens; the live map is a link at the top and the bottom (Oct 12) @both", async ({ page }) => {
  // a summary laid out as a flex box loses its disclosure triangle: the closed section read as a
  // heading over nothing ("the how we measured is blank", Oct 12)
  await setUp(page);
  await reportData(page);
  await page.goto(REPORT);
  const method = page.locator("details.method");
  await expect(method).not.toHaveAttribute("open");
  const marker = await method.locator("summary").evaluate((e) => {
    const m = getComputedStyle(e, "::before");
    return { content: m.content, w: parseFloat(m.width) + parseFloat(m.borderRightWidth), h: parseFloat(m.height) + parseFloat(m.borderBottomWidth) };
  });
  expect(marker.content).not.toBe("none");
  expect(marker.w).toBeGreaterThan(5);
  expect(marker.h).toBeGreaterThan(5);
  await expect(method.locator("dd").first()).toBeHidden();
  await method.getByText("How we measured this").click();
  await expect(method.locator("dd").first()).toBeVisible();
  await expect(method.locator("dd").first()).toContainText("every 30 seconds");
  // the way to the live map: the header's pill and the band at the end
  await expect(page.getByRole("link", { name: "See every bus live" })).toHaveAttribute("href", "/");
  await expect(page.getByRole("link", { name: "Open chitransit.fyi" })).toHaveAttribute("href", "/");
});

test("report: a reroutes-map ring says when its stop missed its first bus, and that none came back (Oct 12)", async ({ page }) => {
  // MapLibre drops null properties: a stop that had no bus all morning arrived with no `from`,
  // and the popup read "from 12 PM" (278 of 980 rings). Now `from` is its first missed bus: #10
  // starts after 8:45 AM on a Sunday
  await setUp(page);
  await reportData(page);
  await page.goto(REPORT);
  await page.locator(".reroutes").scrollIntoViewIfNeeded();
  await page.getByRole("combobox", { name: "Your route", exact: true }).click(); // the route explorer's picker
  await page.getByRole("option", { name: "#10 Obama Presidential Center/Museum of Science & Industry Express", exact: true }).click();
  await page.waitForFunction(() => {
    const m = (window as unknown as { reroutesMap?: { loaded(): boolean; getLayer(id: string): unknown } }).reroutesMap;
    return !!m && m.loaded() && !!m.getLayer("skipped");
  }, undefined, { timeout: 30_000 });
  await page.locator(".reroutes .map").scrollIntoViewIfNeeded(); // the route's card above it pushes it down
  const at = await page.evaluate(() => {
    const m = (window as unknown as { reroutesMap: { jumpTo(o: object): void; project(p: [number, number]): { x: number; y: number }; getContainer(): HTMLElement } }).reroutesMap;
    m.jumpTo({ center: [-87.62772, 41.87707], zoom: 16 });
    const p = m.project([-87.62772, 41.87707]);
    const r = m.getContainer().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  });
  await page.waitForTimeout(500);
  await page.mouse.click(at.x, at.y);
  const popup = page.locator(".maplibregl-popup-content");
  await expect(popup).toContainText("State & Van Buren: no #10 southbound bus from 9:04 AM to the end of the day");
});

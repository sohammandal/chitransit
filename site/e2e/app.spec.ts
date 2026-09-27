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

test("route: lines per direction, key, yesterday adds up, map shows only this route", async ({ page }) => {
  const errors = await setUp(page);
  await page.goto("/route/66/");
  await expect(page.getByRole("heading", { name: "Chicago", exact: true })).toBeVisible();
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await expect(page.getByText("Arriving together")).toBeVisible();

  const [ran, notice, never, unclear] = await page.locator(".day dd").allInnerTexts();
  const scheduled = Number((await page.getByText(/trips scheduled/).innerText()).split(" ")[0]);
  expect(Number(ran) + Number(notice) + Number(never) + Number(unclear)).toBe(scheduled);

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
  await expect(page.getByText(/since the bus ahead passed here|its trip, at the|no bus ahead/)).toBeVisible();
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

test("nightly files missing: the live parts still work", async ({ page }) => {
  const errors = await setUp(page, ["routes.json", "headways.json", "summary.json"]);
  await page.goto("/route/66/");
  await expect(page.locator(".sheet svg")).toHaveCount(2);
  await expect(page.getByText(/trips scheduled/)).toHaveCount(0);
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

test("search: typing a route fits the map to its buses", async ({ page }) => {
  await setUp(page);
  await page.goto("/");
  await busesDrawn(page);
  await page.getByLabel("Go to a route").fill("79");
  await page.getByLabel("Go to a route").press("Enter");
  await expect(page).toHaveURL(/\/route\/79\/$/);
  await page.waitForTimeout(1500); // the map eases to the route
  const outside = await page.evaluate(() => {
    const m = window.map as unknown as { getBounds(): { contains(p: [number, number]): boolean } };
    const buses = (window as unknown as { __feed: { vehicles: { rt: string; lon: number; lat: number }[] } }).__feed;
    return buses.vehicles.filter((v) => v.rt === "79" && !m.getBounds().contains([v.lon, v.lat])).length;
  });
  expect(outside).toBe(0);
});

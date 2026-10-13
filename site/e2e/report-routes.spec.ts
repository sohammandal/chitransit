// "What happened to your route": the route explorer (picker, card, reroutes map: RouteExplorer.svelte,
// ReroutesMap.svelte) and the per-route timeline (RouteTimeline.svelte), kept in step by pickedRoute.
// Fixtures: timeline.json has all 34 routes; reroutes.json only #10, #22 and #36.
import { expect, test, type Page } from "@playwright/test";
import { REPORT, reportClean, reportPage } from "./report";

type Map = {
  loaded(): boolean;
  isMoving(): boolean;
  getLayer(id: string): unknown;
  getFilter(id: string): unknown;
  getBounds(): { getWest(): number; getEast(): number; getSouth(): number; getNorth(): number };
};

const explorer = (page: Page) => page.locator("#route-explorer");
const picker = (page: Page) => page.getByRole("combobox", { name: "Your route", exact: true });
const list = (page: Page) => page.getByRole("listbox", { name: "Your route", exact: true });
const card = (page: Page) => explorer(page).locator(".card");
const row = (page: Page, name: string) => page.getByRole("button", { name, exact: true });

/** Pick in the picker's list: "#22 Clark", "All 34 routes". */
async function choose(page: Page, name: string) {
  await picker(page).click();
  await list(page).getByRole("option", { name, exact: true }).click();
}

/** The reroutes map, made and drawn, at rest. */
async function mapReady(page: Page) {
  await page.waitForFunction(() => {
    const m = (window as unknown as { reroutesMap?: Map }).reroutesMap;
    return !!m && m.loaded() && !!m.getLayer("skipped") && !m.isMoving();
  }, undefined, { timeout: 30_000 });
}

/** The map's view: west, south, east, north. */
const view = (page: Page) => page.evaluate(() => {
  const b = (window as unknown as { reroutesMap: Map }).reroutesMap.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
});

test("report: picking #22 shows its card with its name and numbers, and the map shows it alone (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(REPORT);
  await explorer(page).scrollIntoViewIfNeeded();
  await mapReady(page);
  const all = await view(page);
  await choose(page, "#22 Clark");

  await expect(card(page).locator(".title")).toHaveText("#22 Clark");
  await expect(card(page)).toContainText("About 11,000 rides on an average Sunday");
  await expect(card(page).locator(".cut")).toHaveText("63 of its 152 stops cut off");
  await expect(card(page).locator(".dirs li")).toHaveText([/Northbound\s*34 of 79/, /Southbound\s*29 of 73/]);
  // its day in order: the stops losing and getting back a bus between CTA's two clocks
  await expect(card(page).locator(".day li")).toHaveText([
    "6:02 AMFirst missed #22 bus at half its cut-off stops",
    "6:30 AMCTA's race-day alert begins",
    "7:04 AMDetour flag on in Bus Tracker",
    "3:06 PMDetour flag off",
    "3:46 PMA #22 bus again at half its cut-off stops",
    "4 PMCTA's race-day alert ends",
    "4:10 PMA #22 bus again at all its cut-off stops",
  ]);
  expect(new URL(page.url()).searchParams.get("route")).toBe("22"); // a link to it

  // the map: #22's lines, stops and waiting places alone, fitted to them (Clark St, Lakeview to the Loop)
  await mapReady(page);
  const filter = await page.evaluate(() => (window as unknown as { reroutesMap: Map }).reroutesMap.getFilter("skipped"));
  expect(filter).toEqual(["==", ["get", "rt"], "22"]);
  const [w, s, e, n] = await view(page);
  for (const [got, edge] of [[w, -87.66218], [s, 41.87224]]) expect(got).toBeLessThanOrEqual(edge); // #22's own extent
  for (const [got, edge] of [[e, -87.62497], [n, 41.95467]]) expect(got).toBeGreaterThanOrEqual(edge);
  expect(n - s).toBeLessThan(all[3] - all[1]);

  // the timeline follows
  await expect(row(page, "#22 Clark")).toHaveAttribute("aria-pressed", "true");

  // back to all routes: the map, the card and the address too
  await choose(page, "All 34 routes");
  await expect(card(page)).toHaveCount(0);
  await expect(explorer(page)).toContainText("All 34 rerouted routes are on the map");
  await mapReady(page);
  expect(await page.evaluate(() => (window as unknown as { reroutesMap: Map }).reroutesMap.getFilter("skipped"))).toBeFalsy(); // every route
  expect(new URL(page.url()).searchParams.has("route")).toBe(false);
  await expect(row(page, "#22 Clark")).toHaveAttribute("aria-pressed", "false");
  await reportClean(page, errors);
});

test("report: the picker opens the page's own list on the picked route; a tap, arrows, typing and Enter pick; Escape or a press elsewhere closes it (Oct 12) @both", async ({ page }) => {
  // the browser's own select opened its system menu: on a computer a long list in 19 px bold
  // with a tick, unlike the stop finder's (Oct 12)
  const errors = await reportPage(page);
  await page.goto(`${REPORT}?route=22`);
  await expect(picker(page)).toHaveText("#22 Clark");
  await expect(list(page)).toBeHidden();
  await picker(page).click();
  await expect(list(page)).toBeVisible();
  await expect(picker(page)).toHaveAttribute("aria-expanded", "true");
  await expect(list(page).getByRole("option")).toHaveCount(35);
  await expect(list(page).getByRole("option").first()).toHaveAccessibleName("All 34 routes");
  await expect(list(page).getByRole("option", { selected: true })).toHaveAccessibleName("#22 Clark");
  // on the screen: the whole list, and the picked route in it
  const box = (await list(page).boundingBox())!;
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  await expect(list(page).getByRole("option", { name: "#22 Clark", exact: true })).toBeInViewport({ ratio: 1 });

  // a tap on a row picks it and closes the list
  await list(page).getByRole("option", { name: "#36 Broadway", exact: true }).click();
  await expect(list(page)).toBeHidden();
  await expect(picker(page)).toHaveText("#36 Broadway");
  await expect(card(page).locator(".title")).toHaveText("#36 Broadway");

  // keys: Down opens it, typing a number goes to that route, Enter picks it
  const on = async (name: RegExp) => expect(picker(page)).toHaveAttribute("aria-activedescendant", (await list(page).getByRole("option", { name }).getAttribute("id"))!);
  await picker(page).focus();
  await page.keyboard.press("ArrowDown");
  await expect(list(page)).toBeVisible();
  await page.keyboard.type("10");
  await on(/^#10 Obama/);
  await page.keyboard.press("Enter");
  await expect(list(page)).toBeHidden();
  await expect(card(page).locator(".title")).toHaveText(/^#10 Obama/);
  // or a word of its name; Escape closes it without picking
  await page.keyboard.press("ArrowDown");
  await page.keyboard.type("broad");
  await on(/^#36 Broadway$/);
  await page.keyboard.press("ArrowDown");
  await on(/^#50 Damen$/);
  await page.keyboard.press("Escape");
  await expect(list(page)).toBeHidden();
  await expect(picker(page)).toHaveText(/^#10 Obama/);
  await expect(picker(page)).toBeFocused();

  // a press anywhere else closes it
  await picker(page).click();
  await expect(list(page)).toBeVisible();
  await explorer(page).locator(".label").click();
  await expect(list(page)).toBeHidden();
  await expect(picker(page)).toHaveText(/^#10 Obama/);
  await reportClean(page, errors);
});

test("report: the reroutes map's key is there before its data, so the page below doesn't jump when it comes (Oct 12) @both", async ({ page }) => {
  // the key came with the map's data and pushed the page below it down ~70 px as a reader scrolled
  // past: the crossings chart moved under a finger mid-tap (Oct 12)
  const errors = await reportPage(page);
  let release = () => {};
  const held = new Promise<void>((r) => (release = r));
  await page.route("**/data/reports/marathon-2026/reroutes.json", async (r) => { await held; await r.fallback(); });
  await page.goto(REPORT);
  await explorer(page).scrollIntoViewIfNeeded(); // near: the map asks for its data
  await expect(explorer(page)).toContainText("All 34 rerouted routes are on the map");
  await expect(page.locator(".reroutes .key li")).toHaveCount(5);
  const below = page.getByRole("heading", { name: "Almost no buses crossed the course all morning", exact: true });
  const at = async () => (await below.boundingBox())!.y + (await page.evaluate(() => scrollY));
  const before = await at();
  release();
  await mapReady(page);
  expect(await at()).toBe(before);
  await reportClean(page, errors);
});

test("report: tapping a timeline row picks that route: the card and the picker follow; again: all routes (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(REPORT);
  const timeline = page.locator(".timeline");
  await timeline.scrollIntoViewIfNeeded();
  await row(page, "#36 Broadway").click();
  await expect(row(page, "#36 Broadway")).toHaveAttribute("aria-pressed", "true");
  await expect(timeline.locator(".summary")).toContainText(
    "#36 Broadway: detour flag 7:08 AM to 3:06 PM; half its 73 cut-off stops had a #36 bus again by 3:59 PM, all by 4:28 PM",
  );
  // the other rows dim
  await expect(timeline.locator("svg g.dim")).toHaveCount(33);
  await expect(picker(page)).toHaveText("#36 Broadway");
  await expect(card(page).locator(".title")).toHaveText("#36 Broadway");
  await expect(card(page).locator(".cut")).toHaveText("73 of its 142 stops cut off");

  // "Map" goes to the explorer, showing the same route
  await timeline.getByRole("button", { name: "Map", exact: true }).click();
  await expect(explorer(page)).toBeInViewport();

  // tapping the picked row again shows all routes
  await timeline.scrollIntoViewIfNeeded();
  await row(page, "#36 Broadway").click();
  await expect(row(page, "#36 Broadway")).toHaveAttribute("aria-pressed", "false");
  await expect(timeline.locator("svg g.dim")).toHaveCount(0);
  await expect(picker(page)).toHaveText("All 34 routes");
  await reportClean(page, errors);
});

test("report: the timeline's rows take one Tab stop; arrows move between them and Enter picks (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(REPORT);
  await page.locator(".timeline").scrollIntoViewIfNeeded();
  const rows = page.locator(".timeline .row");
  await expect(rows).toHaveCount(34);
  await expect(page.locator('.timeline .row[tabindex="0"]')).toHaveCount(1);
  await row(page, "#152 Addison").focus(); // the first row: CTA lifted its detour first
  await page.keyboard.press("ArrowDown");
  await expect(row(page, "#80 Irving Park")).toBeFocused();
  await page.keyboard.press("End");
  await expect(row(page, "J14 Jeffery Jump")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(row(page, "J14 Jeffery Jump")).toHaveAttribute("aria-pressed", "true");
  await expect(picker(page)).toHaveText("J14 Jeffery Jump");
  await expect(page.locator('.timeline .row[tabindex="0"]')).toHaveText(/J14/);
  // the close button on the line under the chart shows all routes again
  await page.getByRole("button", { name: "Show all routes" }).click();
  await expect(picker(page)).toHaveText("All 34 routes");
  await reportClean(page, errors);
});

test("report: a linked route opens picked and in view; a route whose stops never got a bus back says so (Oct 12)", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(`${REPORT}?route=10`);
  await expect(picker(page)).toHaveText("#10 Obama Presidential Center/Museum of Science & Industry Express");
  await expect(explorer(page)).toBeInViewport();
  await expect(card(page).locator(".title")).toHaveText("#10 Obama Presidential Center/Museum of Science & Industry Express");
  await expect(card(page).locator(".cut")).toHaveText("6 of its 15 stops cut off");
  // its own alerts: a multi-day one (Oct 8 to Oct 12, Balbo to Roosevelt) as well as race day's;
  // the card said "CTA's alerts end 4 PM" for every route (Oct 12)
  await expect(card(page).locator(".day li").first()).toHaveText("4 AMOct 8CTA's multi-day Marathon alert begins");
  await expect(card(page).locator(".day li").filter({ hasText: "multi-day Marathon alert ends" })).toHaveText("6 AMOct 12CTA's multi-day Marathon alert ends");
  // its stops lost their buses at its first bus of the day (#10 starts after 8:45 AM on a Sunday)
  await expect(card(page).locator(".day li").filter({ hasText: "First missed" })).toHaveText("9:05 AMFirst missed #10 bus at half its cut-off stops");
  await expect(card(page).locator(".day li").last()).toHaveText("No #10 bus again at its cut-off stops that day");
  await expect(card(page)).toContainText("Detour flag still on the next morning");
  await expect(page.locator(".timeline .summary")).toContainText("its 6 cut-off stops had no #10 bus again that day");
  // big enough to tap on a phone, and nothing wider than the screen
  expect((await picker(page).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  for (const b of await page.locator(".timeline .summary button").all()) expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await reportClean(page, errors);
});

test("report: a picked route shows its whole path thin under what changed, beyond the view (Oct 12) @both", async ({ page }) => {
  // alone, the few blocks that changed read as the route: #66 cut short at Chicago & Orleans looked
  // like it never went west to Austin (Oct 12)
  const errors = await reportPage(page);
  await page.goto(`${REPORT}?route=22`);
  await page.locator(".reroutes .map").scrollIntoViewIfNeeded(); // the card above pushes it down on a phone
  await mapReady(page);
  await expect(explorer(page).locator(".key")).toContainText("Ran as usual");
  const { shown, inView, outside } = await page.evaluate(() => {
    const m = (window as unknown as { reroutesMap: Map & { querySourceFeatures(s: string): { properties: { rt: string; kind: string }; geometry: { coordinates: [number, number][] } }[] } }).reroutesMap;
    const b = m.getBounds();
    const route = m.querySourceFeatures("lines").filter((f) => f.properties.rt === "22" && f.properties.kind === "route");
    const pts = route.flatMap((f) => f.geometry.coordinates);
    const inside = (p: [number, number]) => p[0] >= b.getWest() && p[0] <= b.getEast() && p[1] >= b.getSouth() && p[1] <= b.getNorth();
    return { shown: JSON.stringify(m.getFilter("route")), inView: pts.some(inside), outside: pts.some((p) => !inside(p)) };
  });
  expect(shown).toContain('"22"');
  expect(inView && outside).toBe(true); // it runs on off the map: the view stays on what changed
  // all routes again: no thin lines
  await choose(page, "All 34 routes");
  await expect(explorer(page).locator(".key")).not.toContainText("Ran as usual");
  await reportClean(page, errors);
});

test("report: a waiting place says how many buses sat there between trips and for how long in all (Oct 12) @both", async ({ page }) => {
  // "buses spent 21 hours waiting here in all (routes 66)" read as one bus waiting 21 hours, and
  // "buses waited here" as riders waiting (Oct 12)
  const errors = await reportPage(page);
  await page.goto(REPORT);
  await explorer(page).scrollIntoViewIfNeeded();
  await mapReady(page);
  const at = await page.evaluate(() => {
    const m = (window as unknown as { reroutesMap: { jumpTo(o: object): void; project(p: [number, number]): { x: number; y: number }; getContainer(): HTMLElement } }).reroutesMap;
    m.jumpTo({ center: [-87.6294, 41.89298], zoom: 15 });
    const p = m.project([-87.6294, 41.89298]);
    const r = m.getContainer().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  });
  await mapReady(page);
  await page.mouse.click(at.x, at.y);
  await expect(page.locator(".maplibregl-popup-content")).toHaveText(
    "Dearborn & Ontario: 87 buses (#22, #36, #146, #147 and #151) sat here between trips, 82 hours in all from 7 AM to 6 PM. On a normal Sunday, almost none.",
  );
  await reportClean(page, errors);
});

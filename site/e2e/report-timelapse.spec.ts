// The Marathon retro's hero map (Timelapse.svelte): its moments, finding a stop, a stop's day.
import { expect, test, type Page } from "@playwright/test";
import { REPORT, fixture, reportClean, reportPage } from "./report";

type TestMap = { loaded(): boolean; isMoving(): boolean; getZoom(): number; getCenter(): { lng: number; lat: number } };
const clockText = (page: Page) => page.locator(".timelapse .readout strong");
const day = (page: Page) => page.locator(".timelapse .day");
const box = (page: Page) => page.getByRole("combobox", { name: "Find your stop" });
const sides = (page: Page) => day(page).locator(".side");

/** The map has drawn and stopped moving: a camera to check, or a point to tap. */
async function settled(page: Page) {
  await page.waitForFunction(() => {
    const m = (window as unknown as { marathonMap?: TestMap }).marathonMap;
    return !!m && m.loaded() && !m.isMoving();
  }, undefined, { timeout: 30_000 });
}

test("report: the timelapse's moments jump its clock, and stop it playing (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  // the peak: the frame with the most dark stops in the fixture (the data moves with the pass rule)
  const st = JSON.parse(fixture("reports/marathon-2026/stops.json")!.toString()) as { start: number; step: number; dark: number[] };
  const top = Math.max(...st.dark), at = (i: number) => {
    const m = st.start + i * st.step, h = Math.floor(m / 60) % 12 || 12;
    return `${h}${m % 60 ? `:${String(m % 60).padStart(2, "0")}` : ""} ${m < 720 ? "AM" : "PM"}`;
  };
  const peak = at(st.dark.indexOf(top));
  await page.goto(`${REPORT}?at=6:00`);
  await expect(clockText(page)).toHaveText("6 AM");
  const moments = page.getByRole("group", { name: "Moments of the day" }).getByRole("button");
  // fixed facts, then the data's: the peak (most dark stops) and the first frame after it under
  // a normal Sunday's noise. CTA's race-day alerts ended at 4 PM, not all its alerts: five routes
  // had multi-day ones too (Oct 12)
  await expect(moments).toHaveText(["6 AM course closes", "7:20 AM race starts", `${peak} peak`, "4 PM race-day alerts end", "6:40 PM back to normal"]);
  await expect(moments.first()).toHaveAttribute("aria-current", "true"); // the slider sits on 6 AM
  await page.getByRole("button", { name: `${peak} peak` }).click();
  await expect(clockText(page)).toHaveText(peak);
  await expect(page.locator(".timelapse .readout")).toContainText(`${top} stops without their buses`);
  await expect(page.getByRole("button", { name: `${peak} peak` })).toHaveAttribute("aria-current", "true");
  await expect(moments.first()).not.toHaveAttribute("aria-current", /.*/);
  // a moment stops the play where it lands
  await page.getByRole("button", { name: "Play" }).click();
  await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
  await page.getByRole("button", { name: "6:40 PM back to normal" }).click();
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
  await expect(clockText(page)).toHaveText("6:40 PM");
  await reportClean(page, errors);
});

test("report: finding a stop shows its day: the map goes there, a strip and a sentence (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(`${REPORT}?at=12:40`);
  await settled(page);
  await expect(page.getByRole("button", { name: "Clear the stop" })).toBeHidden(); // nothing to clear yet
  // words in any order, part of a word as typed
  await box(page).fill("randolph sta");
  await page.getByRole("option", { name: "State & Randolph" }).click();
  await expect(box(page)).toHaveValue("State & Randolph");
  // its own routes' buses, to the minute: the last #29 at 5:13 AM, the next at 5:48 PM, and the
  // routes out longer than that (once "from about 5:20 AM to 5:50 PM", from 10-minute frames)
  await expect(day(page).locator(".says")).toHaveText("No #10, #29 or #146 bus from 5:13 AM to 5:48 PM; #146 not back until 7:34 PM, #10 not back that day");
  await expect(day(page).locator("svg")).toHaveCount(1);
  await expect(sides(page)).toHaveCount(0); // one stop: no label
  await expect(page).toHaveURL(/[?&]stop=State\+%26\+Randolph/);
  await settled(page);
  const cam = await page.evaluate(() => {
    const m = (window as unknown as { marathonMap: TestMap }).marathonMap;
    return { zoom: m.getZoom(), ...m.getCenter() };
  });
  expect(cam.zoom).toBeGreaterThan(14.5);
  expect(Math.abs(cam.lng - -87.62781)).toBeLessThan(0.001);
  expect(Math.abs(cam.lat - 41.88436)).toBeLessThan(0.001);

  // its cursor sits on the clock's frame (12:40 PM: frame 46 of 114)
  expect(await day(page).locator(".cursor").evaluate((e) => parseFloat((e as HTMLElement).style.left))).toBeCloseTo((46.5 / 114) * 100, 1);

  // same-named stops (both directions) are one place, a row each, labelled by where their buses
  // go; arrows and Return pick
  await box(page).fill("van buren state");
  await expect(page.getByRole("option", { name: /State & Van Buren\s*2 stops/ })).toBeVisible();
  await box(page).press("ArrowDown");
  await expect(page.getByRole("option", { name: /State & Van Buren/ })).toHaveAttribute("aria-selected", "true");
  await box(page).press("Enter");
  await expect(day(page).locator("svg")).toHaveCount(2);
  await expect(sides(page)).toHaveText(["Northbound", "Southbound"]);
  await expect(day(page)).toContainText("Northbound: No #10, #29, #146 or #147 bus from 5:09 AM to 5:41 PM; #147 not back until 6:28 PM, #146 until 6:31 PM, #10 until 6:36 PM");
  await expect(day(page)).toContainText("Southbound: No bus of its 5 routes from 5:46 AM to 5:41 PM; no #62 bus from 5:07 AM to 5:57 PM, #6 not back until 6:22 PM");
  // each row's cursor on the clock's frame too
  await expect(day(page).locator(".cursor")).toHaveCount(2);

  // Escape closes the suggestions; a name with nothing to match says so
  await box(page).fill("sta");
  await expect(page.getByRole("listbox")).toBeVisible();
  await box(page).press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await box(page).fill("zzz");
  await expect(page.getByRole("listbox")).toContainText("No stop on this map matches “zzz”");

  // clearing it: no strip, the map back over the whole course, the address without it
  await page.getByRole("button", { name: "Clear the stop" }).click();
  await expect(day(page)).toHaveCount(0);
  await expect(box(page)).toHaveValue("");
  await expect(page.getByRole("button", { name: "Clear the stop" })).toBeHidden();
  await expect(page).not.toHaveURL(/stop=/);
  await settled(page);
  expect(await page.evaluate(() => (window as unknown as { marathonMap: TestMap }).marathonMap.getZoom())).toBeLessThan(13);
  await reportClean(page, errors);
});

test("report: ?stop= opens the timelapse on that stop, any case (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(`${REPORT}?at=12:40&stop=sheridan%20%26%20ainslie`);
  await expect(box(page)).toHaveValue("Sheridan & Ainslie");
  await expect(day(page)).toContainText("Southbound: Buses as usual all day");
  await expect(clockText(page)).toHaveText("12:40 PM");
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible(); // a linked moment doesn't play
  await settled(page);
  const cam = await page.evaluate(() => {
    const m = (window as unknown as { marathonMap: TestMap }).marathonMap;
    return { zoom: m.getZoom(), ...m.getCenter() };
  });
  expect(cam.zoom).toBeGreaterThan(14.5);
  expect(Math.abs(cam.lng - -87.6549)).toBeLessThan(0.001);
  expect(Math.abs(cam.lat - 41.97152)).toBeLessThan(0.001);
  // a stop of several stretches says which one it tells
  await page.goto(`${REPORT}?at=12:40&stop=Michigan%20%26%20Erie`);
  await expect(day(page)).toContainText("No bus of its 5 routes from 7:33 AM to 11:17 AM, the longest of 3 stretches; no #151 bus from 5:22 AM to 11:17 AM");
  // a stop that has buses only some hours on a normal Sunday isn't "as usual all day": 14227
  // Washington & State is #22's at night (Oct 12)
  await page.goto(`${REPORT}?at=12:40&stop=Washington%20%26%20State`);
  await expect(day(page)).toContainText("#22 Northbound: Buses as usual, at the hours it normally has them");
  await expect(day(page)).toContainText("#20, #56, #60, #124, #151 and J14: No bus of its 6 routes from 6:06 AM to 5:14 PM");
  // a last bus in the small hours was Saturday night's: said so, not "from 12:05 AM" (Oct 12)
  await page.goto(`${REPORT}?at=12:40&stop=Michigan%20%26%20Chicago`);
  await expect(day(page)).toContainText("until 6:21 PM (the last came at 12:05 AM)");
  await expect(day(page)).not.toContainText("from 12:05");
  await reportClean(page, errors);
});

test("report: a stop's rows are labelled by where their buses go, never by where the stop sits (Oct 12) @both", async ({ page }) => {
  // "West"/"East" by position read as westbound/eastbound, and were the opposite of the buses'
  // way at 321 of 813 two-stop corners (the north-side stop of an east-west street is westbound's)
  const errors = await reportPage(page);
  // CTA's name, where it is also the way they go: Clark & Dickens's west stop is #22 and #36 southbound
  await page.goto(`${REPORT}?at=12:40&stop=Clark%20%26%20Dickens`);
  await expect(sides(page)).toHaveText(["Northbound", "Southbound"]);
  await expect(day(page)).toContainText("Southbound: No #22 or #36 bus from 5:38 AM to 3:38 PM");
  // CTA calls #60 on Racine eastbound and westbound while it runs north and south there: the
  // route goes with it ("Northbound" alone would be the wrong stop for half of its riders)
  await page.goto(`${REPORT}?at=12:40&stop=Racine%20%26%20Taylor`);
  await expect(sides(page)).toHaveText(["#60 Eastbound", "#60 Westbound"]);
  await expect(day(page)).toContainText("#60 Eastbound: No #60 bus from 5:47 AM to 6 PM");
  await expect(day(page)).toContainText("#60 Westbound: No #60 bus from 5:27 AM to 7:05 PM");
  // both Washington & State stops are eastbound: told apart by their routes, not "West"/"East"
  await page.goto(`${REPORT}?at=12:40&stop=Washington%20%26%20State`);
  await expect(sides(page)).toHaveText(["#20, #56, #60, #124, #151 and J14", "#22 Northbound"]);
  // a stop no route serves on Sundays: where it stands, said as a stop
  await page.goto(`${REPORT}?at=12:40&stop=Harrison%20%26%20Damen`);
  await expect(sides(page)).toHaveText(["#126 Westbound", "East stop"]);
  await expect(day(page)).toContainText("East stop: Not a regular Sunday stop; #126 and #9 buses came by");
  await reportClean(page, errors);
});

test("report: a stop's sentence comes from its own routes' buses, not the strip's frames (Oct 12)", async ({ page }) => {
  const errors = await reportPage(page);
  // its only route out while others' buses came: once "One of its routes cut ... the longest of
  // 6 stretches", as if it had more routes (8 such stops, Oct 12)
  await page.goto(`${REPORT}?at=12:40&stop=Harrison%20%26%20Paulina`);
  await expect(day(page)).toContainText("Westbound: No #126 bus from 6:41 AM to 6:17 PM (#8 and #9 buses came by)");
  await expect(day(page)).not.toContainText("One of its routes");
  // a rerouted route's buses don't end its own routes' stretch: J14s used State & Balbo all
  // day, its #29 and #146 came back at 5:36 PM (once the sentence ended at the first J14)
  await page.goto(`${REPORT}?at=12:40&stop=State%20%26%20Balbo`);
  await expect(day(page).locator(".says")).toHaveText("No #29 or #146 bus from 5:07 AM to 5:36 PM (J14 buses came by 7:41 AM–5:31 PM); #146 not back until 6:27 PM");
  // none of its route since midnight: "until"
  await page.goto(`${REPORT}?at=12:40&stop=Cermak%20%26%20Morgan`);
  await expect(day(page)).toContainText("Eastbound: No #21 bus until 6:07 PM");
  // red on the strip, but its own detoured buses passed within a few meters: can't tell, never
  // "as usual" and never "no bus"
  await page.goto(`${REPORT}?at=12:40&stop=Armitage%20%26%20Halsted`);
  await expect(day(page)).toContainText("Eastbound: Can't tell: #73 buses came close 7:35 AM–2:10 PM, maybe without stopping");
  await reportClean(page, errors);
});

test("report: on a phone, a tap on a suggestion picks it and puts the keyboard away (Oct 12)", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(`${REPORT}?at=12:40`);
  await settled(page);
  await box(page).tap();
  await box(page).pressSequentially("harrison pau");
  await page.getByRole("option", { name: "Harrison & Paulina" }).tap();
  await expect(day(page)).toContainText("Westbound: No #126 bus from 6:41 AM to 6:17 PM");
  await expect(box(page)).not.toBeFocused();
  // the labels and sentences fit the phone: nothing wider than the page (reportClean) and the
  // strips keep the slider's column, so their cursors line up with its
  const [strip, slider] = await Promise.all([day(page).locator("svg").first().boundingBox(), page.getByRole("slider", { name: "Time of day" }).boundingBox()]);
  expect(Math.abs(strip!.x - slider!.x)).toBeLessThan(1);
  expect(Math.abs(strip!.width - slider!.width)).toBeLessThan(1);
  await reportClean(page, errors);
});

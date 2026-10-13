// The report's charts: the time charts' card (hover, tap, drag, keys) and numbered moments
// (TimeChart.svelte, via SameBuses and Crossings), and the bars drawn without JavaScript (Bars.svelte).
import { expect, test, type Locator, type Page } from "@playwright/test";
import { fixture, REPORT, reportClean, reportPage } from "./report";

type Moment = { at: number; label: string };
const json = (name: string) => JSON.parse(String(fixture(`reports/marathon-2026/${name}`)));
const series = json("series.json") as { start: number; step: number; events: Moment[]; M: { buses: number[]; stops: number[]; stops_band: [number, number][] } };
const crossings = json("crossings.json") as { start: number; step: number; events: Moment[]; cross: number[]; under: number[]; cross_band: [number, number][] };

const sameBuses = (page: Page) => page.getByRole("img", { name: /buses out stayed near 100% of a normal Sunday/ });
const crossingChart = (page: Page) => page.getByRole("img", { name: /Buses crossing the Marathon course/ });
const card = (chart: Locator) => chart.locator(".card");
const pct = (v: number) => `${Math.round(v)}%`;

// "1–1:15 PM", "11:45 AM–noon", "11:45 PM–midnight": minutes after midnight at the bin's start
function binStart(when: string): number {
  const [a, b] = when.split("–");
  if (a === "noon") return 720;
  const m = /^(\d{1,2})(?::(\d\d))?(?: ([AP]M))?$/.exec(a);
  expect(m, `a bin's time: ${when}`).not.toBeNull();
  const ap = m![3] ?? / ([AP]M)$/.exec(b)?.[1];
  return ((+m![1] % 12) + (ap === "PM" ? 12 : 0)) * 60 + +(m![2] ?? 0);
}

/** The card is showing bin i of the Marathon-routes chart: its time, both lines and the band. */
async function showsSameBusesBin(chart: Locator): Promise<number> {
  const when = (await card(chart).locator(".when").textContent())!;
  const i = (binStart(when) - series.start) / series.step;
  expect(Number.isInteger(i) && i >= 0 && i < series.M.buses.length, `${when} is a bin`).toBe(true);
  const text = await card(chart).textContent();
  expect(text).toContain(`${pct(series.M.buses[i])}Buses out`);
  expect(text).toContain(`${pct(series.M.stops[i])}Bus visits to usual stops`);
  const [lo, hi] = series.M.stops_band[i].map(Math.round);
  expect(text).toContain(`${lo === hi ? lo : `${lo}–${hi}`}%Visits on Sep 27 and Oct 4`);
  return i;
}

async function inside(inner: Locator, outer: Locator) {
  const [a, b] = [(await inner.boundingBox())!, (await outer.boundingBox())!];
  expect(a.x).toBeGreaterThanOrEqual(b.x - 0.5);
  expect(a.x + a.width).toBeLessThanOrEqual(b.x + b.width + 0.5);
  expect(a.y).toBeGreaterThanOrEqual(b.y - 0.5);
  expect(a.y + a.height).toBeLessThanOrEqual(b.y + b.height + 0.5);
}

test("report: hovering a time chart shows a card with the time, each line's value and the normal range, kept inside the chart (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(REPORT);
  const chart = sameBuses(page);
  await chart.scrollIntoViewIfNeeded();
  const box = (await chart.boundingBox())!;
  const seen: number[] = [];
  for (const fx of [0.02, 0.5, 0.99]) {
    await chart.hover({ position: { x: box.width * fx, y: box.height / 2 } });
    await expect(card(chart)).toBeVisible();
    seen.push(await showsSameBusesBin(chart));
    await inside(card(chart), chart); // flipped or shifted at the edges, never past them
    await expect(chart.locator("line.cross")).toHaveCount(1);
  }
  expect(seen[0]).toBe(0);
  expect(seen[2]).toBe(series.M.buses.length - 1);
  expect(seen[1]).toBeGreaterThan(seen[0]);
  // the pointer leaves: the card goes
  await page.mouse.move(box.x + box.width / 2, box.y - 150);
  await expect(card(chart)).toBeHidden();
  await reportClean(page, errors);
});

test("report: on a phone a tap shows a time chart's card, a sideways drag moves it, the page still scrolls, a tap elsewhere hides it (Oct 12)", async ({ page }, info) => {
  test.skip(info.project.name !== "phone", "touch");
  const errors = await reportPage(page);
  await page.goto(REPORT);
  const chart = crossingChart(page);
  await chart.scrollIntoViewIfNeeded();
  const box = (await chart.boundingBox())!;
  await chart.tap({ position: { x: box.width * 0.3, y: box.height / 2 } });
  await expect(card(chart)).toBeVisible();
  const before = binStart((await card(chart).locator(".when").textContent())!);
  // a sideways drag scrubs (touch events, as a finger sends them)
  const cdp = await page.context().newCDPSession(page);
  const touch = (type: "touchStart" | "touchMove" | "touchEnd", x: number) =>
    cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x, y: box.y + box.height / 2, id: 1 }] });
  await touch("touchStart", box.x + box.width * 0.3);
  // paced like a finger (~20 ms a move): sent at once, a slower runner merged them and the card
  // stopped short (CI, Oct 13)
  for (let k = 1; k <= 8; k++) {
    await touch("touchMove", box.x + box.width * (0.3 + (0.5 * k) / 8));
    await page.waitForTimeout(20);
  }
  await touch("touchEnd", 0);
  const when = card(chart).locator(".when");
  await expect.poll(async () => binStart((await when.textContent())!)).toBeGreaterThan(before + 6 * 60); // half the chart on
  const i = (binStart((await when.textContent())!) - crossings.start) / crossings.step;
  const text = await card(chart).textContent();
  expect(text).toContain(`${crossings.cross[i]}At street level`);
  expect(text).toContain(`${crossings.under[i]}Under it, on an expressway`);
  await inside(card(chart), chart);
  // lifting the finger leaves it up; a tap anywhere else puts it away
  await expect(card(chart)).toBeVisible();
  await chart.locator("xpath=..").locator(".title").tap();
  await expect(card(chart)).toBeHidden();
  // a vertical swipe on the chart still scrolls the page, as one on its title does. Headless Linux
  // (CI) fakes no touch scroll at all, there the title's doesn't move either: then this proves
  // nothing and is skipped (CI, Oct 13; the paced drag above was a misread of that failure)
  const swipe = async (on: Locator) => {
    const b = (await on.boundingBox())!, y0 = await page.evaluate(() => scrollY);
    await cdp.send("Input.synthesizeScrollGesture", { x: b.x + b.width / 2, y: b.y + b.height / 2, yDistance: -200, gestureSourceType: "touch", speed: 1200 });
    await page.waitForTimeout(400);
    return (await page.evaluate(() => scrollY)) - y0;
  };
  await chart.evaluate((el) => el.scrollIntoView({ block: "center" }));
  if ((await swipe(chart.locator("xpath=..").locator(".title"))) > 50) expect(await swipe(chart)).toBeGreaterThan(50);
  await reportClean(page, errors);
});

test("report: the keyboard moves a time chart's crosshair, and each step is read out (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(REPORT);
  const chart = sameBuses(page);
  await chart.scrollIntoViewIfNeeded();
  await expect(chart).toHaveAttribute("tabindex", "0");
  await chart.focus();
  const live = chart.locator("xpath=..").locator("[aria-live]");
  const when = card(chart).locator(".when");
  await page.keyboard.press("Home");
  await expect(when).toHaveText("4–4:15 AM");
  await expect(live).toHaveText(`4 to 4:15 AM: Buses out ${pct(series.M.buses[0])}, Bus visits to usual stops ${pct(series.M.stops[0])}, Visits on Sep 27 and Oct 4 ${Math.round(series.M.stops_band[0][0])} to ${Math.round(series.M.stops_band[0][1])}%`);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(when).toHaveText("4:30–4:45 AM");
  await page.keyboard.press("Shift+ArrowRight"); // an hour
  await expect(when).toHaveText("5:30–5:45 AM");
  await expect(live).toContainText("5:30 to 5:45 AM: Buses out");
  expect(await showsSameBusesBin(chart)).toBe(6);
  await page.keyboard.press("End");
  await expect(when).toHaveText("11:45 PM–midnight");
  await page.keyboard.press("ArrowLeft");
  await expect(when).toHaveText("11:30–11:45 PM");
  // a bin with a moment in it names the moment
  const m = series.events[0];
  await page.keyboard.press("Home");
  for (let k = 0; k < Math.floor((m.at - series.start) / series.step); k++) await page.keyboard.press("ArrowRight");
  await expect(card(chart).locator(".moment-now")).toHaveText(`1${m.label}`);
  await page.keyboard.press("Escape");
  await expect(card(chart)).toBeHidden();
  await reportClean(page, errors);
});

test("report: a time chart numbers its moments on the chart and labels them under it, readable and apart at phone width (Oct 12) @both", async ({ page }) => {
  const errors = await reportPage(page);
  await page.goto(REPORT);
  for (const [chart, events] of [[sameBuses(page), series.events], [crossingChart(page), crossings.events]] as const) {
    await chart.scrollIntoViewIfNeeded();
    const figure = chart.locator("xpath=..");
    const pins = chart.locator("circle.pin");
    await expect(pins).toHaveCount(events.length);
    await expect(chart.locator("line.moment")).toHaveCount(events.length);
    const boxes = await Promise.all((await pins.all()).map(async (p) => (await p.boundingBox())!));
    boxes.slice(1).forEach((b, k) => expect(b.x, "badges apart").toBeGreaterThanOrEqual(boxes[k].x + boxes[k].width));
    await expect(chart.locator("text.pin-n")).toHaveText(events.map((_, k) => String(k + 1)));
    const legend = figure.locator("ol.moments li");
    await expect(legend).toHaveText([...events].sort((a, b) => a.at - b.at).map((e, k) => `${k + 1}${e.label}`));
    const viewport = page.viewportSize()!;
    for (const li of await legend.all()) {
      await expect(li).toBeVisible();
      const b = (await li.boundingBox())!;
      expect(b.x + b.width).toBeLessThanOrEqual(viewport.width);
      expect(await li.evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(13);
    }
    // a title and what the numbers are, above the chart
    await expect(figure.locator(".title")).toHaveText(/Buses/);
    await expect(figure.locator(".sub")).toContainText(/% of the two Sundays before|Crossings per 15 minutes/);
  }
  await reportClean(page, errors);
});

test.describe("without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("report: bar charts are drawn in the page's HTML, each bar as long as its value (Oct 12) @both", async ({ page }) => {
    await page.goto(REPORT);
    const charts = page.locator(".bars");
    await expect(charts).toHaveCount(2); // the waits, and the stops with no bus
    for (const chart of await charts.all()) {
      await chart.scrollIntoViewIfNeeded();
      const rows = chart.locator("li");
      expect(await rows.count()).toBeGreaterThan(1);
      const drawn = [];
      for (const row of await rows.all()) {
        await expect(row.locator(".value")).toBeVisible();
        const [bar, track, value] = await Promise.all([".bar", ".track", ".value"].map(async (s) => (await row.locator(s).boundingBox())!));
        expect(value.x + value.width, "the value fits after its bar").toBeLessThanOrEqual(track.x + track.width + 0.5);
        drawn.push({ width: bar.width, value: Number(await row.getAttribute("data-value")) });
      }
      const longest = drawn.reduce((a, b) => (b.value > a.value ? b : a));
      for (const d of drawn) expect(Math.abs(d.width - (longest.width * d.value) / longest.value)).toBeLessThan(2);
      await expect(chart.locator("li.hi")).toHaveCount(1);
    }
  });
});


test("report: a moment's number sits in the middle of its badge, by pixels (Oct 12) @both", async ({ page }) => {
  // Overpass draws digits ~2 px high in their line box: the list's numbers sat high (Oct 12)
  const errors = await reportPage(page);
  await page.goto(REPORT);
  const pin = page.locator("ol.moments b.pin").first();
  await pin.scrollIntoViewIfNeeded();
  await expect(pin).toBeVisible();
  const png = (await pin.screenshot({ scale: "device" })).toString("base64");
  const off = await page.evaluate(async ([src, dpr]) => {
    const img = new Image();
    img.src = "data:image/png;base64," + src;
    await img.decode();
    const c = document.createElement("canvas");
    [c.width, c.height] = [img.width, img.height];
    const g = c.getContext("2d")!;
    g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    const lum = (x: number, y: number) => { const i = (y * c.width + x) * 4; return 0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]; };
    const r = c.width / 2;
    let top = Infinity, bottom = -1; // the digit: light ink inside the circle
    for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++)
      if ((x - r) ** 2 + (y - r) ** 2 < (r * 0.8) ** 2 && lum(x, y) > 200) [top, bottom] = [Math.min(top, y), Math.max(bottom, y)];
    return ((top + bottom) / 2 - (c.height - 1) / 2) / dpr;
  }, [png, await page.evaluate(() => devicePixelRatio)] as const);
  expect(Math.abs(off)).toBeLessThan(0.75);
  await reportClean(page, errors);
});

test("report: icons, dots and bars sit on their text's centre, not the line box's (Oct 12) @both", async ({ page }) => {
  // Overpass draws letters ~1.5-2 px high in their line box, so everything centred on the box sat
  // low beside its text: the summary's chevron, the header pill's dot, every key, the route card's
  // dots, the bars; and text sat high in the pill, the button, the picker (Oct 12, measured)
  const errors = await reportPage(page);
  await page.goto(`${REPORT}?route=22`);
  for (const sel of [".timelapse .key", ".reroutes .map", "#route-explorer .day", ".bars", "figure.chart .key"])
    await page.locator(sel).first().scrollIntoViewIfNeeded();
  await expect(page.locator(".reroutes .key li").first()).toBeVisible();
  await expect(page.locator("#route-explorer .day li").first()).toBeVisible();
  await page.getByRole("link", { name: "Open source", exact: true }).hover(); // the footer's pill
  await page.locator("#route-pick").click(); // its list open: the picked row's tick
  await expect(page.locator("#route-list svg")).toBeVisible();
  // within 1 px where the nudges were measured (macOS); Linux sets Overpass up to ~1.2 px
  // differently (CI, Oct 13), so there 1.5 px: still well short of the 2 px misses this catches
  const tol = process.platform === "darwin" ? 1 : 1.5;
  const off = await page.evaluate(async (tol) => {
    await document.fonts.ready;
    const ctx = document.createElement("canvas").getContext("2d")!;
    const capMid = (el: Element) => {
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.textContent!.trim() ? 1 : 3) });
      const node = w.nextNode()!;
      const r = document.createRange();
      r.selectNodeContents(node);
      const rect = r.getClientRects()[0];
      const cs = getComputedStyle(node.parentElement!);
      ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const m = ctx.measureText("H");
      const base = rect.top + (rect.height - (m.fontBoundingBoxAscent + m.fontBoundingBoxDescent)) / 2 + m.fontBoundingBoxAscent;
      return base - m.actualBoundingBoxAscent / 2;
    };
    const mid = (el: Element) => { const b = el.getBoundingClientRect(); return b.top + b.height / 2; };
    const out: [string, number][] = [];
    const live = document.querySelector("nav .live")!;
    out.push(["pill dot", mid(live.querySelector("i")!) - capMid(live)], ["pill text", capMid(live) - mid(live)]);
    const cta = document.querySelector(".cta a")!;
    out.push(["button text", capMid(cta) - mid(cta)]);
    // the footer's hover pill: room around the text (it had 2 px sides, Oct 12) and the text centred
    const foot = [...document.querySelectorAll(".foot-links a")].find((a) => a.textContent === "Open source")!;
    const words = document.createRange();
    words.selectNodeContents(foot);
    const [fb, wb] = [foot.getBoundingClientRect(), words.getBoundingClientRect()];
    out.push(["footer pill text", capMid(foot) - mid(foot)]);
    if (Math.min(wb.left - fb.left, fb.right - wb.right) < 12) out.push(["footer pill sides", 99]);
    const pick = document.querySelector("#route-pick")!, row = document.querySelector('#route-list [aria-selected="true"]')!;
    out.push(["picker text", capMid(pick) - mid(pick)], ["list row text", capMid(row) - mid(row)], ["list tick", mid(row.querySelector("svg")!) - capMid(row)]);
    for (const li of document.querySelectorAll(".timelapse .key li, .reroutes .key li, figure.chart .key li"))
      out.push([`key: ${li.textContent!.trim()}`, mid(li.querySelector("i, .swatch")!) - capMid(li)]);
    for (const li of document.querySelectorAll("#route-explorer .day li"))
      out.push(["card dot", li.getBoundingClientRect().top + parseFloat(getComputedStyle(li, "::before").top) + 5 - capMid(li.querySelector("time")!)]);
    for (const li of document.querySelectorAll(".bars li"))
      out.push(["bar", mid(li.querySelector(".bar")!) - capMid(li.querySelector(".name")!)]);
    return out.filter(([, d]) => Math.abs(d) > tol).map(([k, d]) => `${k}: ${d.toFixed(2)} px`);
  }, tol);
  expect(off).toEqual([]);
  await reportClean(page, errors);
});

// The link-preview image (public/og.jpg): a live route view from prod, 1200x630 at 2x.
// Usage: node scripts/og.mjs <route> public/og.jpg 2   (pick a route with buses together and long waits)
import { chromium } from "playwright";
const [rt, out, scale] = process.argv.slice(2);
const b = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const p = await (await b.newContext({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: Number(scale) })).newPage();
await p.goto(`https://chitransit.fyi/route/${rt}/`, { waitUntil: "networkidle" });
await p.waitForTimeout(15000);
await p.screenshot({ path: out, ...(out.endsWith(".jpg") ? { type: "jpeg", quality: 88 } : {}) });
await b.close();

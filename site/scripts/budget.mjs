// Size budget: fail the build if the JavaScript a visitor downloads grows past the limit
// (gzipped, as served). Baseline 2026-09-27: page 293 KB (mostly MapLibre), worker 141 KB.
// Raise a limit only on purpose, in the same commit as the change that needs it.
// 2026-10-11: page 320 -> 340 KB for the 'L' (trains on the map, line strips, station and train
// cards: ~25 KB; 318 KB used then).
// 2026-10-12: counted per page (the scripts a page names, then every file they import), not as
// the sum of every file in the build: the report's charts load only on the report page.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BUDGET_KB = { page: 340, worker: 160 };
const dist = new URL("../dist/", import.meta.url);
const astro = new URL("_astro/", dist);

const kb = new Map(
  readdirSync(astro)
    .filter((f) => f.endsWith(".js"))
    .map((f) => [f, gzipSync(readFileSync(new URL(f, astro))).length / 1024]),
);
// a file named in quotes: static and dynamic imports ("./x.js") and the worker's URL (`/_astro/x.js`)
const named = (text) => [...text.matchAll(/["'`](?:\.\/|\/_astro\/)([\w.-]+\.js)["'`]/g)].map((m) => m[1]).filter((f) => kb.has(f));
const imports = new Map([...kb.keys()].map((f) => [f, named(readFileSync(new URL(f, astro), "utf8"))]));

function loads(html) {
  const seen = new Set();
  const visit = (f) => !seen.has(f) && (seen.add(f), imports.get(f).forEach(visit));
  named(html).forEach(visit);
  return [...seen];
}

const pages = readdirSync(dist, { recursive: true })
  .filter((f) => f.endsWith(".html"))
  .map((f) => {
    const files = loads(readFileSync(new URL(f, dist), "utf8"));
    const sum = (worker) => files.filter((g) => g.includes("worker") === worker).reduce((t, g) => t + kb.get(g), 0);
    return { page: f, used: { page: sum(false), worker: sum(true) }, files };
  });

const loaded = new Set(pages.flatMap((p) => p.files));
const unused = [...kb.keys()].filter((f) => !loaded.has(f));
if (unused.length) console.log(`(no page loads ${unused.join(", ")})`);

let over = false;
for (const [part, limit] of Object.entries(BUDGET_KB)) {
  const top = pages.reduce((a, b) => (b.used[part] > a.used[part] ? b : a));
  const ok = top.used[part] <= limit;
  over ||= !ok;
  console.log(`${ok ? "ok  " : "OVER"} ${part}: ${top.used[part].toFixed(1)} KB of ${limit} KB (gzip), largest on /${top.page}`);
}
if (over) process.exit(1);

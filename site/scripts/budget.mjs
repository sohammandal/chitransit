// Size budget: fail the build if the JavaScript a visitor downloads grows past the limit
// (gzipped, as served). Baseline 2026-09-27: page 293 KB (mostly MapLibre), worker 141 KB.
// Raise a limit only on purpose, in the same commit as the change that needs it.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BUDGET_KB = { page: 320, worker: 160 };
const dir = new URL("../dist/_astro/", import.meta.url);
const sizes = readdirSync(dir)
  .filter((f) => f.endsWith(".js"))
  .map((f) => [f, gzipSync(readFileSync(new URL(f, dir))).length / 1024]);
const sum = (worker) => sizes.filter(([f]) => f.includes("worker") === worker).reduce((t, [, s]) => t + s, 0);
const used = { page: sum(false), worker: sum(true) };

let over = false;
for (const [part, limit] of Object.entries(BUDGET_KB)) {
  const ok = used[part] <= limit;
  over ||= !ok;
  console.log(`${ok ? "ok  " : "OVER"} ${part}: ${used[part].toFixed(1)} KB of ${limit} KB (gzip)`);
}
if (over) process.exit(1);

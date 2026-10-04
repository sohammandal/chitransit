// chitransit.fyi: static output, deployed to a private S3 bucket behind CloudFront.
import { existsSync, readFileSync } from "node:fs";
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import svelte from "@astrojs/svelte";

const PROD = "https://www.chitransit.fyi";

// Dev only: serve prod's live feed (read-only, public). If prod runs an older poller than
// this checkout expects, fill in what it lacks from local files so new UI can be tried
// before the poller ships; today that's `near` (the nearest stop, from public/data patterns).
// LIVE=local instead serves a local poller's feed from public/live (make poll with
// LIVE_TARGET=site/public/live/vehicles.json).
function liveFeedInDev() {
  const stops = new Map();
  const patternStops = (pid) => {
    if (!stops.has(pid)) {
      const f = `public/data/v1/patterns/${pid}.json`;
      stops.set(pid, existsSync(f) ? JSON.parse(readFileSync(f, "utf8")).s : null);
    }
    return stops.get(pid);
  };
  return {
    name: "live-feed-in-dev",
    configureServer(server) {
      if (process.env.LIVE === "local") return;
      server.middlewares.use("/live/vehicles.json", async (_req, res) => {
        try {
          const feed = await (await fetch(`${PROD}/live/vehicles.json`, { cache: "no-store" })).json();
          for (const v of feed.vehicles) {
            if (v.near !== undefined || v.pdist == null) continue;
            const s = patternStops(v.pid);
            v.near = s?.length ? s.reduce((a, b) => (Math.abs(b[3] - v.pdist) < Math.abs(a[3] - v.pdist) ? b : a))[2] : null;
          }
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(feed));
        } catch (e) {
          res.statusCode = 502;
          res.end(String(e));
        }
      });
      server.middlewares.use("/live/stops.json", async (_req, res) => {
        const r = await fetch(`${PROD}/live/stops.json`, { cache: "no-store" }).catch(() => null);
        res.statusCode = r?.status ?? 502; // until prod's poller writes it: none, and the stop view says nothing
        res.setHeader("Content-Type", "application/json");
        res.end(r ? await r.text() : "");
      });
    },
  };
}

export default defineConfig({
  site: PROD.replace("www.", ""),
  output: "static",
  integrations: [svelte(), sitemap()],
  devToolbar: { enabled: false }, // dev should look like prod
  build: { format: "directory" },
  // Content Security Policy as a <meta> tag on every page, with hashes for Astro's own inline
  // scripts (a header can't know them). frame-ancestors can't go in a meta tag: CloudFront sends
  // it (infra/terraform/prod/site.tf). e2e runs every view under this policy.
  security: {
    csp: {
      directives: [
        "default-src 'self'",
        "img-src 'self' data: blob: https://tiles.openfreemap.org",
        "connect-src 'self' https://tiles.openfreemap.org",
        "font-src 'self'",
        "worker-src 'self' blob:",
        "child-src blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
      ],
      scriptDirective: { resources: ["'self'"] },
      // style="" attributes (Svelte, MapLibre markers); style elements stay hashed
      styleDirective: { resources: ["'self'", { resource: "'unsafe-inline'", kind: "attribute" }] },
    },
  },
  // Dev: the nightly files come from prod too, so localhost shows what the site shows.
  // DATA=local serves public/data instead (transform/site_data.py --out site/public/data),
  // to try a change to those files before the nightly publishes it.
  vite: {
    worker: { format: "es" }, // MapLibre starts its worker with type: "module"
    plugins: [liveFeedInDev()],
    server: {
      proxy: process.env.DATA === "local" ? {} : { "/data": { target: PROD, changeOrigin: true } },
    },
  },
});

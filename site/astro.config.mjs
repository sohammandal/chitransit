// chitransit.fyi: static output, deployed to a private S3 bucket behind CloudFront.
import { defineConfig } from "astro/config";
import svelte from "@astrojs/svelte";

export default defineConfig({
  site: "https://chitransit.fyi",
  output: "static",
  integrations: [svelte()],
  devToolbar: { enabled: false }, // dev should look like prod
  build: { format: "directory" },
  // Dev: the live feed and route shapes come from prod (read-only, public); the other
  // nightly files come from public/data (transform/site_data.py). No AWS credentials needed.
  vite: {
    worker: { format: "es" }, // MapLibre starts its worker with type: "module"
    server: {
      proxy: {
        // LIVE=local: read a local poller's feed from public/live instead (make poll with
        // LIVE_TARGET=site/public/live/vehicles.json).
        ...(process.env.LIVE === "local" ? {} : { "/live": { target: "https://www.chitransit.fyi", changeOrigin: true } }),
        "/data/v1/patterns": { target: "https://www.chitransit.fyi", changeOrigin: true },
      },
    },
  },
});

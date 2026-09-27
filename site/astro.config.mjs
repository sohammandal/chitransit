// chitransit.fyi: static output, deployed to a private S3 bucket behind CloudFront.
import { defineConfig } from "astro/config";
import svelte from "@astrojs/svelte";

export default defineConfig({
  site: "https://chitransit.fyi",
  output: "static",
  integrations: [svelte()],
  build: { format: "directory" },
  // Dev: the live feed comes from prod (read-only, public); patterns come from
  // public/data (transform/site_data.py). No AWS credentials needed.
  vite: {
    worker: { format: "es" }, // MapLibre starts its worker with type: "module"
    server: {
      proxy: { "/live": { target: "https://www.chitransit.fyi", changeOrigin: true } },
    },
  },
});

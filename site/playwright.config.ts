// Browser tests (e2e/). Two modes:
//   npm run e2e          the production build (astro preview) with fixed sample data: every view
//                        at phone size, and the tests tagged @both (layout, map) at desktop size
//                        too. Runs in CI before any publish.
//   BASE_URL=... npm run e2e:prod   a short smoke test of a live site with real data (phone size). Runs
//                        right after a deploy; a failure rolls the deploy back (site.yml).
import { defineConfig, devices } from "@playwright/test";

const prod = process.env.BASE_URL;

export default defineConfig({
  testDir: "e2e",
  testMatch: prod ? "smoke.spec.ts" : ["app.spec.ts", "report-*.spec.ts"],
  timeout: 45_000,
  fullyParallel: true, // the tests are independent: spread them over the workers
  workers: process.env.CI ? 1 : undefined, // more starve CI's 2 CPUs: software WebGL map tests time out
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: prod ?? "http://127.0.0.1:4322",
    screenshot: "on",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", grep: /@both/, use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 900 } } },
    { name: "phone", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: prod ? undefined : {
    // Plain static server over the build (astro preview may detach itself from the terminal).
    command: "python3 -m http.server 4322 --bind 127.0.0.1 --directory dist",
    url: "http://127.0.0.1:4322",
    reuseExistingServer: !process.env.CI,
  },
});

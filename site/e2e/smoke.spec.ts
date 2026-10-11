// After a deploy: the live site, real data. Short on purpose; the full checks ran before publish.
import { expect, test } from "@playwright/test";

const build = process.env.EXPECT_BUILD; // the commit that was just published

test("live site: new build, live feed, nightly data, a route page", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto("/");
  if (build) await expect(page.locator('meta[name="build"]')).toHaveAttribute("content", build);
  await expect(page.getByText(/\d+ buses on the road/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Right now" })).toBeVisible();

  for (const f of ["routes.json", "headways.json", "report.json"]) {
    expect((await request.get(`/data/v1/${f}`)).ok(), f).toBe(true);
  }
  // every published file made it, hidden folders included (Oct 4: .well-known didn't)
  for (const f of ["/.well-known/security.txt", "/manifest.json", "/og.jpg", "/sitemap-index.xml", "/about/"]) {
    expect((await request.get(f)).ok(), f).toBe(true);
  }

  const first = page.locator(".sheet .row").first(); // links since Oct 11
  if (await first.count()) {
    await first.click();
    await expect(page).toHaveURL(/\/route\/[^/]+\/$/);
    await expect(page.getByLabel("Back to all routes")).toBeVisible();
  }
  expect(errors).toEqual([]);
});

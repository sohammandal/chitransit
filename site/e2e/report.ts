// Shared by the report pages' browser tests (report-*.spec.ts): their data from e2e/fixtures/reports,
// errors and Content-Security-Policy violations collected for each test.
import { readFileSync, existsSync } from "node:fs";
import { expect, type Page } from "@playwright/test";

const FIX = new URL("./fixtures/", import.meta.url);
export const fixture = (name: string) => (existsSync(new URL(name, FIX)) ? readFileSync(new URL(name, FIX)) : null);
export const REPORT = "/reports/marathon-2026/";

/** Serve the report's data files from the fixtures; collect page errors and CSP violations. */
export async function reportPage(page: Page): Promise<string[]> {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/openfreemap|Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.addInitScript(() => {
    (window as unknown as { __e2e: boolean }).__e2e = true;
    const csp: string[] = ((window as unknown as { __csp: string[] }).__csp = []);
    document.addEventListener("securitypolicyviolation", (e) => csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  await page.route("**/data/reports/**", (r) => {
    const body = fixture(new URL(r.request().url()).pathname.replace("/data/", ""));
    return body ? r.fulfill({ body, contentType: "application/json" }) : r.fulfill({ status: 404 });
  });
  return errors;
}

/** No CSP violation and no horizontal scroll: check at the end of each report test. */
export async function reportClean(page: Page, errors: string[]) {
  const csp = await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? []);
  expect(csp, "CSP violations").toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
}

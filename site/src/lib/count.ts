/** Page views, cookieless: GoatCounter stores no cookies and no IP addresses. Counted on the
 *  real site only (never dev, tests or headless browsers), sent from our own code so the page
 *  loads no third-party script (the CSP allows only this endpoint: astro.config.mjs). */
const ENDPOINT = "https://chitransit.goatcounter.com/count";
let last = "";

export function countView(): void {
  const path = location.pathname;
  if (!/^(www\.)?chitransit\.fyi$/.test(location.hostname) || navigator.webdriver || path === last) return;
  const q = new URLSearchParams({
    p: path,
    t: document.title,
    r: last ? "" : document.referrer, // where the visit came from: its first page only
    s: `${screen.width},${screen.height},${window.devicePixelRatio}`,
    rnd: Math.random().toString(36).slice(2),
  });
  last = path;
  try {
    navigator.sendBeacon(`${ENDPOINT}?${q}`);
  } catch {
    // counting never breaks the page
  }
}

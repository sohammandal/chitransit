// Links. The app's own views are real addresses (<a href>), so search engines can follow them
// and a rider can open one in a new tab; the app opens a plain click itself, without loading a
// page. The one address from outside that becomes a link is CTA's, from its alerts: checked.

/** A plain click: the app opens it in place. ⌘/Ctrl/Shift/Alt-clicks and the middle button
 *  (a new tab or window) are left to the browser. */
export function plainClick(e: MouseEvent): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

/** A route's address, or the city's. */
export function routeUrl(rt: string | null): string {
  return rt ? `/route/${encodeURIComponent(rt)}/` : "/";
}

/** An alert's link from the live feed, only if it's on CTA's own site over https; null shows the
 *  headline as plain text. The feed passes CTA's link through, so a tampered feed (CTA's or our
 *  box's) can't put any other address on our pages. */
export function ctaLink(u: string | null | undefined): string | null {
  if (!u) return null;
  try {
    const url = new URL(u);
    const cta = url.hostname === "transitchicago.com" || url.hostname.endsWith(".transitchicago.com");
    return url.protocol === "https:" && cta ? url.href : null;
  } catch {
    return null; // not an address at all
  }
}

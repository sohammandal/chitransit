// Distances along the route, from CTA's own feet-along-the-pattern (pdist): where a bus is,
// never when it will arrive.
import type { Pattern, Vehicle } from "./types";

/** "2.1 mi", or "under 0.1 mi" */
export function milesText(feet: number): string {
  const mi = feet / 5280;
  return mi < 0.1 ? "under 0.1 mi" : `${mi.toFixed(1)} mi`;
}

/** Feet along its pattern of stop `stpid` (null: not on it). */
function stopFeet(p: Pattern | undefined, stpid: string | null): number | null {
  if (!p || !stpid) return null;
  const s = p.s.find((x) => x[5] === stpid);
  return s ? s[3] : null;
}

/** Feet along the route from `behind` up to `ahead`, or null when it can't be measured. On
 *  different patterns (a short turn, a branch) it goes through the stop `ahead` heads for. */
export function feetBetween(ahead: Vehicle, behind: Vehicle, patterns: Map<number, Pattern>): number | null {
  if (ahead.pdist == null || behind.pdist == null || ahead.pid == null || behind.pid == null) return null;
  let d: number;
  if (ahead.pid === behind.pid) d = ahead.pdist - behind.pdist;
  else {
    const fa = stopFeet(patterns.get(ahead.pid), ahead.next_stpid);
    const fb = stopFeet(patterns.get(behind.pid), ahead.next_stpid);
    if (fa == null || fb == null) return null;
    d = fb - behind.pdist - (fa - ahead.pdist);
  }
  return d >= 0 ? d : null;
}

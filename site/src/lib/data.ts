// The nightly data files, each fetched once per page view (they change daily), and the
// poller's pattern files (live/patterns/), which the site reads first.
import type { Headways, Lines, Pattern, Report, Stop } from "./types";

const BASE = "/data/v1";
const cache = new Map<string, Promise<unknown>>();

function load<T>(path: string): Promise<T> {
  let p = cache.get(path);
  if (!p) {
    p = fetch(`${BASE}/${path}`).then((r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    });
    p.catch(() => cache.delete(path)); // a failed load can be retried
    cache.set(path, p);
  }
  return p as Promise<T>;
}

export const routeNames = () => load<Record<string, string>>("routes.json");
export const headways = () => load<Headways>("headways.json");
export const report = () => load<Report>("report.json");
const live = new Map<number, Promise<Pattern>>();
/** A pattern's shape and stops: the poller's own file first (every pattern, a reroute's from the
 *  minute it starts), the nightly's if that's missing. Neither yet: asked again next time. */
export function pattern(pid: number): Promise<Pattern> {
  let p = live.get(pid);
  if (!p) {
    p = fetch(`/live/patterns/${pid}.json`)
      .then((r) => (r.ok ? (r.json() as Promise<Pattern>) : Promise.reject(new Error(`HTTP ${r.status}`))))
      .catch(() => load<Pattern>(`patterns/${pid}.json`));
    p.catch(() => live.delete(pid));
    live.set(pid, p);
  }
  return p;
}
export const stops = () => load<Stop[]>("stops.json");
let linesFile: Promise<Lines> | null = null;
/** The 'L' as drawn (l/lines.json, part of the site: no nightly behind it), loaded once, the
 *  first time trains are shown. */
export function lines(): Promise<Lines> {
  if (!linesFile) {
    linesFile = fetch("/l/lines.json").then((r) => (r.ok ? (r.json() as Promise<Lines>) : Promise.reject(new Error(`HTTP ${r.status}`))));
    linesFile.catch(() => (linesFile = null));
  }
  return linesFile;
}

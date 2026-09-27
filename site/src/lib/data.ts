// The nightly data files, each fetched once per page view (they change daily).
import type { Headways, Pattern, Report } from "./types";

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
export const pattern = (pid: number) => load<Pattern>(`patterns/${pid}.json`);

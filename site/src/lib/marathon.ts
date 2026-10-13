// The Marathon retro (/reports/marathon-2026/): its data files and the helpers its visuals share.
// The files are derived by transform/reports/marathon_2026.py and published to the site
// bucket's data/reports/marathon-2026/ (never by a site deploy).
import { writable } from "svelte/store";

export const DATA = "/data/reports/marathon-2026";

/** The route a reader picked (route explorer, timeline rows): shared by every visual on the page. */
export const pickedRoute = writable<string>("");

/** A moment marked on the charts: minutes after midnight, and what happened. */
export type Moment = { at: number; label: string };

const cache = new Map<string, Promise<unknown>>();

/** A data file, fetched once per page (a failed fetch can be retried). */
export function load<T>(name: string): Promise<T> {
  let p = cache.get(name) as Promise<T> | undefined;
  if (!p) {
    p = fetch(`${DATA}/${name}`).then((r) => {
      if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
      return r.json() as Promise<T>;
    });
    p.catch(() => cache.delete(name));
    cache.set(name, p);
  }
  return p;
}

/** Minutes after midnight as riders say it: "6 AM", "noon", "3:40 PM", "midnight". */
export function clock(min: number): string {
  const m = ((Math.round(min) % 1440) + 1440) % 1440;
  const h = Math.floor(m / 60), mm = m % 60;
  if (mm === 0 && h === 12) return "noon";
  if (mm === 0 && h === 0) return "midnight";
  const h12 = h % 12 || 12;
  return `${h12}${mm ? `:${String(mm).padStart(2, "0")}` : ""} ${h < 12 ? "AM" : "PM"}`;
}

/** stops.json: every stop that normally sees buses, one state per frame, and what the stop finder
 *  says of it (transform/reports/marathon_2026_dark.py: frames, stop_days). */
export type StopFrames = {
  start: number; // minutes after midnight of frame 0
  step: number; // minutes between frames
  frames: number;
  bounds: [[number, number], [number, number]]; // [[west, south], [east, north]]: where the map opens
  stops: [
    number, number, string, // lon, lat, name
    string, // a state per frame: "." quiet, "s" served, "d" no bus, "p" a route cut (another bus came), "u" can't tell
    string, // its usual route-directions, route and CTA's direction letter: "22N,36N" ("": none on both normal Sundays)
    string, // the way they go there, compass letters from their patterns ("N"; CTA's #60 "Westbound" goes "S" on Racine)
    // its Marathon (null: nothing to tell, or a shuttle stop), minutes after midnight (past 1440: after midnight):
    // from-to its longest stretch without a bus of its usual routes (from null: none before that day; to null:
    // none by 4 AM), n such stretches, cut: [route-direction, from, to] its routes out longer (from null unless
    // gone 10+ min sooner), came: [route, first, last, buses] other routes' buses in it. n = 0: no such stretch, but
    // cut has each route-direction's own longest, or (no usual routes) came has every bus of the day. n = -1: can't
    // tell (red on the strip, but buses of its routes came close: came)
    [number | null, number | null, number, [string, number | null, number | null][], [string, number, number, number][]] | null,
  ][];
  dark: number[]; // stops without their buses (dark), per frame
  noise: number[]; // the same count on a normal Sunday judged against the other
  field?: ([number, number] | null)[]; // where the runners were: miles along the course, last to first
};

/** course.json: the Marathon's course, with the distance along it (miles) at each point. */
export type Course = { line: [number, number][]; at: number[] };

/** timeline.json: per rerouted route, CTA's two clocks and the buses (minutes after midnight). */
export type Timeline = {
  alerts: [number, number]; // the window CTA's race-day Customer Alerts gave (every route's)
  routes: {
    rt: string;
    flag: [number, number | null]; // Bus Tracker's detour flag on, off (null: still on next morning)
    out: number; // when its cut-off stops lost their buses
    back: [number, number | null, number] | null; // first, half of them all (null: under half), last to see a bus again
    never?: number; // how many of them saw none that day
    cut: number; // how many stops it cut off
    name: string; // CTA's name for the route ("Clark")
    rides: number | null; // its average Sunday rides (City of Chicago data portal)
    alerts?: [number, number][]; // its own Marathon alerts, minutes after Oct 11's midnight (a multi-day one runs from < 0 or to > 1440)
  }[];
};

/** reroutes.json: CTA's Marathon reroutes as driven, their skipped stops, where buses waited. */
export type Reroutes = {
  bounds: [[number, number], [number, number]];
  routes: { rt: string; dir: string; stops: [number, number]; km: { not_driven: number }; back: [number, number, number] | null }[];
  lines: { type: "Feature"; properties: { rt: string; dir: string; kind: "route" | "skipped" | "instead" }; geometry: { type: "LineString"; coordinates: [number, number][] } }[];
  stops: [string, string, number, number, string, number | null, number | null][]; // rt, dir, lon, lat, name, first missed bus, first back (null: not that day)
  terminals: { name: string; lon: number; lat: number; hours: number; normal: number; routes: string[]; buses: number }[]; // hours: all its buses' waiting, summed
};

/** series.json: per 15 minutes, % of a normal Sunday (the band: each normal Sunday's own). */
export type Series = {
  start: number;
  step: number;
  events: Moment[];
  M: { buses: (number | null)[]; stops: (number | null)[]; buses_band: ([number, number] | null)[]; stops_band: ([number, number] | null)[] };
  O: { buses: (number | null)[]; stops: (number | null)[]; buses_band: ([number, number] | null)[]; stops_band: ([number, number] | null)[] };
};

/** crossings.json: buses crossing the course per 15 minutes (counts). */
export type CrossingSeries = {
  start: number;
  step: number;
  events: Moment[];
  cross: number[]; // Oct 11, at street level
  cross_band: [number, number][]; // the two normal Sundays, lower and higher
  under: number[]; // Oct 11, under the course on an expressway
};

/** The course between two distances along it (miles), as a line: where the race was. */
export function slice(c: Course, from: number, to: number): [number, number][] {
  const end = c.at[c.at.length - 1];
  [from, to] = [Math.min(Math.max(from, 0), end), Math.min(Math.max(to, 0), end)];
  const point = (d: number): [number, number] => {
    const i = Math.max(1, c.at.findIndex((x) => x >= d));
    const f = (d - c.at[i - 1]) / (c.at[i] - c.at[i - 1] || 1);
    const [a, b] = [c.line[i - 1], c.line[i]];
    return [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])];
  };
  return [point(from), ...c.line.filter((_, i) => c.at[i] > from && c.at[i] < to), point(to)];
}

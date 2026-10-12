// The 'L' in the app: CTA's own data shown as CTA sent it (positions, predictions, line
// status), no claims of ours yet. Pure helpers for the line and station views.
import type { Eta, Lines, Train } from "./types";

/** Train Tracker's line codes, in CTA's order, with the names riders use. */
export const LINE_NAMES: Record<string, string> = {
  red: "Red", blue: "Blue", brn: "Brown", g: "Green", org: "Orange", p: "Purple", pink: "Pink", y: "Yellow",
};
// The Loop's stations: a "Loop" destination is the first of these ahead of the train
const LOOP = new Set(["40380", "40260", "41700", "40680", "40850", "40160", "40040", "40730"]);

export const trainKey = (t: { rt: string; rn: string }) => `${t.rt}:${t.rn}`;

/** A line's address: the riders' name for it (/line/brown/), not CTA's code (brn). */
export const lineUrl = (rt: string) => `/line/${(LINE_NAMES[rt] ?? rt).toLowerCase()}/`;

/** The line an address names ("brown" or CTA's "brn"), else null. */
export function lineOf(slug: string): string | null {
  const s = slug.toLowerCase();
  return Object.keys(LINE_NAMES).find((rt) => rt === s || LINE_NAMES[rt].toLowerCase() === s) ?? null;
}

/** A platform board's time: "Due" within a minute (or when CTA says approaching), else minutes;
 *  "Delayed" in place of a time when CTA flags the train as not moving, as CTA's boards do (its
 *  last time is going stale). */
export function etaText(arr: number | null, app: boolean, now: number, dly = false): string {
  if (dly) return "Delayed";
  if (arr == null) return "";
  const min = Math.round((arr - now) / 60);
  return app || min <= 1 ? "Due" : `${min} min`;
}

/** A train's way to its next station: "Arriving" within a minute (or when CTA says
 *  approaching), else "in 3 min"; "Delayed" as on a board. */
export function nextText(arr: number | null, app: boolean, now: number, dly = false): string {
  if (dly) return "Delayed";
  if (arr == null) return "";
  const min = Math.round((arr - now) / 60);
  return app || min <= 1 ? "Arriving" : `in ${min} min`;
}

/** Lines whose name starts with the query ("bl" -> Blue; "line" or "l" alone: none). */
export function matchLines(query: string): string[] {
  const q = query.trim().toLowerCase().replace(/\s+line$/, "");
  if (q.length < 2) return [];
  return Object.entries(LINE_NAMES).filter(([, name]) => name.toLowerCase().startsWith(q)).map(([rt]) => rt);
}

/** Stations left before the train reaches its destination, along one of the line's paths
 *  (a branch, the Loop): null when CTA's destination isn't a station on them. */
export function stationsToGo(t: Train, lines: Lines | null): number | null {
  const line = lines?.lines[t.rt];
  if (!line) return null;
  const isDest = (s: string) => (t.dest === "Loop" ? LOOP.has(s) : lines!.stations[s]?.name === t.dest);
  let best: number | null = null;
  for (const seq of line.stations) {
    // every place the next station appears: a Loop line's path passes some twice (out and back)
    seq.forEach((s, at) => {
      if (s !== t.next) return;
      const end = seq.findIndex((x, i) => i >= at && isDest(x));
      if (end >= 0 && (best == null || end - at < best)) best = end - at;
    });
  }
  return best;
}

export type Direction = { toward: string; trains: Train[] };

/** A line's trains by destination (what their signs say: a branch's own, "Loop"), each in order
 *  along the line, the one nearest its destination first. Grouped by CTA's destination, not its
 *  direction code: Green's two branches share one, and a Loop line's flips inside the Loop. The
 *  groups go by CTA's direction, then the most trains. */
export function lineDirections(trains: Train[], rt: string, lines: Lines | null): Direction[] {
  const groups = new Map<string, Train[]>();
  for (const t of trains) if (t.rt === rt) {
    const toward = t.dest ?? "an unknown destination";
    groups.set(toward, [...(groups.get(toward) ?? []), t]);
  }
  const dir = (ts: Train[]) => Math.min(...ts.map((t) => Number(t.dir) || 9));
  return [...groups.entries()]
    .sort(([, a], [, b]) => dir(a) - dir(b) || b.length - a.length)
    .map(([toward, ts]) => {
      const togo = new Map(ts.map((t) => [t, stationsToGo(t, lines) ?? Infinity]));
      return { toward, trains: [...ts].sort((a, b) => togo.get(a)! - togo.get(b)!) };
    });
}

export type Platform = { label: string; etas: Eta[] };

/** A station's predictions by platform ("Service toward Howard"), each soonest first; the
 *  platforms riders board at first, a terminal's arrivals ("Terminal Arrival") last. */
export function platforms(etas: Eta[]): Platform[] {
  const groups = new Map<string, Eta[]>();
  for (const e of etas) {
    const label = e.stop_desc || `Platform ${e.stop}`;
    groups.set(label, [...(groups.get(label) ?? []), e]);
  }
  const arrivals = (label: string) => (/terminal arrival/i.test(label) ? 1 : 0);
  return [...groups.entries()]
    .sort(([a], [b]) => arrivals(a) - arrivals(b) || a.localeCompare(b))
    .map(([label, es]) => ({ label, etas: [...es].sort((a, b) => (a.arr ?? Infinity) - (b.arr ?? Infinity)) }));
}

export type Strip = {
  seq: string[]; // the stations, in the direction of travel (map ids)
  at: number[]; // each station's place along the strip, 0..1 by distance
  trains: { t: Train; x: number }[]; // the trains placed on it, in order along it
  off: number; // the destination's trains elsewhere (another branch): not placed
};

/** One direction of a line as a strip, toward one destination: its stations in order, at their
 *  real distances, and its trains between their last and next station (by CTA's position). A
 *  Loop line's out-and-back path is cut at the Loop: toward the Loop up to the end of the
 *  circuit, toward the terminal from its start. Of a line's paths, the one most of these trains
 *  are on (Green's branches: the other branch's trains are `off`), then one that ends at their
 *  destination (Purple's shuttle to Howard, not the Express path on past Howard to Linden). */
export function strip(trains: Train[], rt: string, dest: string, lines: Lines | null): Strip | null {
  const line = lines?.lines[rt];
  if (!line || !lines) return null;
  const isDest = (s: string) => (dest === "Loop" ? LOOP.has(s) : lines.stations[s]?.name === dest);
  const runs = line.stations.flatMap((path) => {
    const loop = path.flatMap((s, i) => (LOOP.has(s) ? [i] : []));
    if (path[0] === path.at(-1) && loop.length) {
      return [dest === "Loop" ? path.slice(0, Math.max(...loop) + 1) : path.slice(Math.min(...loop))];
    }
    const end = path.findLastIndex(isDest);
    return end > 0 ? [path.slice(0, end + 1)] : [];
  }).filter((run) => run.some(isDest));
  const mine = trains.filter((t) => t.rt === rt && t.dest === dest);
  const on = (run: string[]) => mine.filter((t) => run.includes(t.next)).length;
  const ends = (run: string[]) => (isDest(run.at(-1)!) ? 1 : 0);
  const seq = runs.sort((a, b) => on(b) - on(a) || ends(b) - ends(a) || b.length - a.length)[0];
  if (!seq) return null;
  const pt = (s: string) => [lines.stations[s].lon, lines.stations[s].lat] as const;
  const cum = [0];
  for (let i = 1; i < seq.length; i++) cum.push(cum[i - 1] + metres(pt(seq[i - 1]), pt(seq[i])));
  const total = cum.at(-1) || 1;
  const placed = mine.flatMap((t) => {
    const j = seq.indexOf(t.next);
    if (j < 0) return [];
    if (j === 0) return [{ t, x: 0 }];
    const f = t.lat == null || t.lon == null ? (t.app ? 0.85 : 0.5) : along(pt(seq[j - 1]), pt(seq[j]), [t.lon, t.lat]);
    return [{ t, x: (cum[j - 1] + f * (cum[j] - cum[j - 1])) / total }];
  });
  return { seq, at: cum.map((c) => c / total), trains: placed.sort((a, b) => a.x - b.x), off: mine.length - placed.length };
}

/** Metres between two points (equirectangular: fine at a city's scale). */
function metres(a: readonly [number, number], b: readonly [number, number]): number {
  const k = Math.cos(((a[1] + b[1]) / 2) * (Math.PI / 180));
  return Math.hypot((b[0] - a[0]) * k, b[1] - a[1]) * 111_320;
}

/** How far along a -> b the point p lies, 0..1 (its projection, clamped). */
function along(a: readonly [number, number], b: readonly [number, number], p: [number, number]): number {
  const k = Math.cos(a[1] * (Math.PI / 180));
  const dx = (b[0] - a[0]) * k, dy = b[1] - a[1];
  const len = dx * dx + dy * dy;
  return len ? Math.max(0, Math.min(1, (((p[0] - a[0]) * k) * dx + (p[1] - a[1]) * dy) / len)) : 1;
}

/** The minutes between each two trains next to each other on a strip: the poller's (ahead_s:
 *  CTA's own times for both, else since the one ahead left), when the one it measured from is
 *  the next on the strip. long / together: our claims (as for buses), the minutes then our
 *  measurement: the wait at its next platform, or how far behind it ran. Two trains bound for
 *  the same platform share one hole: it's the leading one's. */
export type Gap = { from: number; to: number; s: number; long: boolean; together?: boolean };

export function gaps(s: Strip): Gap[] {
  return s.trains.slice(1).flatMap((ahead, i): Gap[] => {
    const behind = s.trains[i], t = behind.t, at = { from: behind.x, to: ahead.x };
    if (t.long_wait && t.gap_s != null && t.stop !== ahead.t.stop) return [{ ...at, s: t.gap_s, long: true }];
    if (t.together_with === trainKey(ahead.t) && t.together_s != null) return [{ ...at, s: t.together_s, long: false, together: true }];
    return t.last_by === trainKey(ahead.t) && t.ahead_s != null ? [{ ...at, s: t.ahead_s, long: false }] : [];
  });
}

/** Trains on a strip that run together (our claim, as for buses): each, and the one ahead. */
export function together(s: Strip): Set<string> {
  const on = new Set(s.trains.map((p) => trainKey(p.t)));
  return new Set(s.trains.flatMap((p) => (p.t.together_with && on.has(p.t.together_with) ? [trainKey(p.t), p.t.together_with] : [])));
}

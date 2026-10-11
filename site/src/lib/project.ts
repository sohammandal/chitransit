// Where a bus on another pattern of the route (a short turn, a branch) sits on the line drawn
// for the main pattern, from its GPS position: the nearest point of the main pattern's shape,
// in feet along it (interpolated between the stops, whose feet Bus Tracker gives). A bus more
// than OFF_LINE_M from the shape isn't on this line (a branch that went its own way).
import type { Pattern, Vehicle } from "./types";

const OFF_LINE_M = 120; // ~400 ft: a street's width and a GPS error, not a parallel street

type Geo = { pts: [number, number][]; along: number[]; stops: { m: number; ft: number }[] };
const cache = new WeakMap<Pattern, Geo>();

function xy(lon: number, lat: number, lat0: number): [number, number] {
  return [lon * Math.cos((lat0 * Math.PI) / 180) * 111_320, lat * 110_540];
}

/** Nearest point of the shape to (x, y): metres along the shape, and metres away from it. */
function locate(g: Geo, x: number, y: number): { m: number; off: number } {
  let best = { m: 0, off: Infinity };
  for (let i = 1; i < g.pts.length; i++) {
    const [ax, ay] = g.pts[i - 1], [bx, by] = g.pts[i];
    const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
    const t = len2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / len2)) : 0;
    const off = Math.hypot(ax + t * dx - x, ay + t * dy - y);
    if (off < best.off) best = { m: g.along[i - 1] + t * Math.sqrt(len2), off };
  }
  return best;
}

function geo(p: Pattern): Geo {
  let g = cache.get(p);
  if (g) return g;
  const lat0 = p.c[0]?.[1] ?? 41.88;
  const pts = p.c.map(([lon, lat]) => xy(lon, lat, lat0));
  const along = [0];
  for (let i = 1; i < pts.length; i++) along.push(along[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  g = { pts, along, stops: [] };
  let last = 0;
  for (const [lon, lat, , ft] of p.s) {
    const m = Math.max(last, locate(g, ...xy(lon, lat, lat0)).m); // in order along the line
    g.stops.push({ m, ft });
    last = m;
  }
  cache.set(p, g);
  return g;
}

/** Feet along the pattern for a position, or null when it isn't on this line. */
export function project(p: Pattern, lat: number | null, lon: number | null): number | null {
  if (lat == null || lon == null || p.c.length < 2 || p.s.length < 2) return null;
  const g = geo(p);
  const { m, off } = locate(g, ...xy(lon, lat, p.c[0][1]));
  if (off > OFF_LINE_M) return null;
  const i = g.stops.findIndex((s) => s.m >= m);
  if (i <= 0) return i === 0 ? g.stops[0].ft : g.stops.at(-1)!.ft;
  const a = g.stops[i - 1], b = g.stops[i];
  return b.m > a.m ? a.ft + ((m - a.m) / (b.m - a.m)) * (b.ft - a.ft) : a.ft;
}

export type LineBus = Vehicle & { other?: boolean }; // other: on another pattern, placed by GPS

/** The buses to draw on pattern `pid`'s line: its own, and those of the route's other
 *  patterns (same direction) that are on it right now, at their place along it. `off`: how
 *  many are elsewhere (a branch or detour). */
export function onLine(p: Pattern, pid: number, buses: Vehicle[]): { drawn: LineBus[]; off: number } {
  const drawn: LineBus[] = [];
  let off = 0;
  for (const b of buses) {
    if (b.pid === pid) {
      drawn.push(b);
      continue;
    }
    const at = project(p, b.lat, b.lon);
    if (at == null) off++;
    else drawn.push({ ...b, pdist: Math.round(at), other: true });
  }
  return { drawn, off };
}

/** The pattern a direction's line is drawn for: the one most of its buses run (with a loaded
 *  shape). The route view and the bus card use the same one, so a bus looks the same in both. */
export function mainPid(buses: Vehicle[], patterns: Map<number, Pattern>): number | undefined {
  const counts = new Map<number, number>();
  for (const b of buses) if (b.pid != null && patterns.has(b.pid)) counts.set(b.pid, (counts.get(b.pid) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0]?.[0];
}

const metres = (a: [number, number], b: [number, number]) => Math.hypot((a[0] - b[0]) * 82_990, (a[1] - b[1]) * 110_540);
/** A pattern's line without its out-and-back spurs to the curb at each stop (A, stop, A → A):
 *  drawn see-through, a spur doubles over itself into a dark dot. A spur returns within 2 m and
 *  is under 30 m (all 26k in CTA's patterns, Sep 29); anything longer is a real turnaround. */
export function withoutSpurs(c: [number, number][]): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i < c.length; i++) {
    const [a, b, n] = [c[i - 1], c[i], c[i + 1]];
    if (a && n && metres(a, n) < 2 && metres(a, b) < 30) { i++; continue; } // skip b and the return
    out.push(b);
  }
  return out;
}

const SAME_STREET_M = 40; // a regular path this close to the reroute's is the same street
/** Metres from point p to the polyline c (flat earth at city scale). */
function toLine(p: [number, number], c: [number, number][]): number {
  let best = Infinity;
  for (let i = 0; i + 1 < c.length; i++) {
    const [ax, ay] = [(c[i][0] - p[0]) * 82_990, (c[i][1] - p[1]) * 110_540];
    const [bx, by] = [(c[i + 1][0] - p[0]) * 82_990, (c[i + 1][1] - p[1]) * 110_540];
    const [dx, dy] = [bx - ax, by - ay];
    const k = dx || dy ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) : 0;
    best = Math.min(best, Math.hypot(ax + k * dx, ay + k * dy));
  }
  return c.length === 1 ? metres(p, c[0]) : best;
}
/** The stretches of a reroute's regular path (CTA's dtrpt, the pattern's d) its buses no longer
 *  take (off its path c), each joined to where it leaves and rejoins it: what CTA's map dashes. */
export function skippedStretches(d: [number, number][], c: [number, number][]): [number, number][][] {
  const out: [number, number][][] = [];
  let run: [number, number][] | null = null;
  d.forEach((p, i) => {
    if (toLine(p, c) > SAME_STREET_M) (run ??= i > 0 ? [d[i - 1]] : []).push(p);
    else if (run) { run.push(p); out.push(run); run = null; }
  });
  if (run) out.push(run);
  return out.filter((r) => r.length > 1);
}

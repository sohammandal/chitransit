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

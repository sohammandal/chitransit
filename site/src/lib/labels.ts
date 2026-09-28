// Street labels for the line view. Text never overlaps: a label is placed only if its box
// clears every label already placed (plus a margin), most important first.

/** The street the route runs on: the name part most of its stops share ("Clark" in "Clark & Belmont"). */
export function ownStreet(names: string[]): string | null {
  const counts = new Map<string, number>();
  for (const n of names) for (const part of n.split("&").map((p) => p.trim().toLowerCase())) {
    if (part) counts.set(part, (counts.get(part) ?? 0) + 1);
  }
  const [best] = [...counts].sort((a, b) => b[1] - a[1]);
  return best && best[1] >= names.length / 2 ? best[0] : null;
}

/** "Clark & Belmont" on Clark -> "Belmont"; names without the route's street stay whole. */
export function crossStreet(name: string, own: string | null): string {
  const parts = name.split("&").map((p) => p.trim());
  if (!own || parts.length !== 2) return name;
  const other = parts.filter((p) => p.toLowerCase() !== own);
  return other.length === 1 ? other[0] : name;
}

/** A stop name for a list row, without the route's own street when the route's name has it:
 *  "Cottage Grove & 60th Street" on Cottage Grove -> "60th Street". */
export function placeName(stop: string, routeName: string | undefined): string {
  const route = (routeName ?? "").toLowerCase();
  const parts = stop.split("&").map((p) => p.trim());
  if (!route || parts.length !== 2) return stop;
  const other = parts.filter((p) => !route.includes(p.toLowerCase()));
  return other.length === 1 ? other[0] : stop;
}

export type Candidate = { x: number; text: string; weight: number };
export type Placed = { x: number; text: string; from: number; to: number };

/** Greedy placement by weight: keeps labels inside [left, right] and `gap` px apart.
 *  charW: an upper bound on the average character width at the label's font size. */
export function place(candidates: Candidate[], left: number, right: number, charW: number, gap = 8, max = 4): Placed[] {
  const placed: Placed[] = [];
  for (const c of [...candidates].sort((a, b) => b.weight - a.weight || a.x - b.x)) {
    if (placed.length >= max) break;
    const w = c.text.length * charW;
    const from = c.x - w / 2, to = c.x + w / 2;
    if (from < left || to > right) continue;
    if (placed.some((p) => from < p.to + gap && to > p.from - gap)) continue;
    placed.push({ x: c.x, text: c.text, from, to });
  }
  return placed.sort((a, b) => a.x - b.x);
}

/** The route's two end names on one row `avail` wide, `gap` apart: both in full when they
 *  fit, else the longer one shortened (with …) first, then both. measure: text → width. */
export function fitEnds(a: string, b: string, avail: number, gap: number, measure: (t: string) => number): [string, string] {
  const cut = (t: string, n: number) => (n >= t.length ? t : `${t.slice(0, n).trimEnd()}…`);
  let na = a.length, nb = b.length;
  const width = () => measure(cut(a, na)) + measure(cut(b, nb)) + gap;
  while (width() > avail && (na > 4 || nb > 4)) {
    if (measure(cut(a, na)) >= measure(cut(b, nb)) && na > 4) na--;
    else if (nb > 4) nb--;
    else na--;
  }
  return [cut(a, na), cut(b, nb)];
}

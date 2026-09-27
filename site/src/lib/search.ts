// Route search: what to suggest as someone types, and which route to open once they pause
// (the exact number if there is one, else the only match). Opening on a pause means typing
// "66" at normal speed never shows #6; a slow "6… 6" shows #6 briefly, which is fine.

export type Match = { rt: string; name: string };

const order = (a: string, b: string) => a.localeCompare(b, "en", { numeric: true });

/** Routes whose number starts with the query, then routes whose name contains it (3+ letters). */
export function matchRoutes(query: string, names: Record<string, string>, limit = 6): Match[] {
  const q = query.trim().toUpperCase();
  if (!q) return [];
  const all = Object.entries(names).map(([rt, name]) => ({ rt, name }));
  const byNumber = all.filter((r) => r.rt.toUpperCase().startsWith(q)).sort((a, b) => order(a.rt, b.rt));
  const byName = q.length >= 3
    ? all.filter((r) => !byNumber.includes(r) && r.name.toUpperCase().includes(q)).sort((a, b) => order(a.rt, b.rt))
    : [];
  return [...byNumber, ...byName].slice(0, limit);
}

/** The route to open when typing pauses: the exact number, else the only match, else none. */
export function toOpen(query: string, names: Record<string, string>): string | null {
  const q = query.trim().toUpperCase();
  const exact = Object.keys(names).find((rt) => rt.toUpperCase() === q);
  if (exact) return exact;
  const all = matchRoutes(query, names, Infinity);
  return all.length === 1 ? all[0].rt : null;
}

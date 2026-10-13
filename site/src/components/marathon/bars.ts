// The numbers behind Bars.svelte, apart from it so they can be tested without a page.

export type BarRow = { label: string; value: number; highlight?: boolean };

/** Each row with its value as printed and its bar's share of the scale (0 to 1, from zero),
 * and the room (em) the longest printed value needs after a full-length bar. */
export function barRows(rows: BarRow[], { unit = "", max, format }: { unit?: string; max?: number; format?: (v: number) => string } = {}) {
  const suffix = /^[a-z]/i.test(unit) ? ` ${unit}` : unit; // "min" -> "9.4 min", "%" -> "76%"
  const text = format ?? ((v: number) => `${v.toLocaleString("en-US", { maximumFractionDigits: 1 })}${suffix}`);
  const top = max ?? Math.max(0, ...rows.map((r) => r.value));
  const out = rows.map((r) => ({ ...r, text: text(r.value), share: top > 0 ? Math.min(Math.max(r.value / top, 0), 1) : 0 }));
  return { rows: out, room: Math.max(0, ...out.map((r) => r.text.length)) * 0.66 + 0.6 };
}

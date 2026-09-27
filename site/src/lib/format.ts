/** "12 min", or "under a minute" */
export function minutes(seconds: number): string {
  const m = Math.round(seconds / 60);
  return m < 1 ? "under a minute" : `${m} min`;
}

/** Chicago calendar date ("2026-09-27") and hour (0-23) of a moment. */
export function chicago(at: Date): { date: string; hour: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23",
  }).formatToParts(at);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return { date: `${get("year")}-${get("month")}-${get("day")}`, hour: Number(get("hour")) };
}

/** "Yesterday" when the date is yesterday in Chicago, else "Sat Sep 26" */
export function dayLabel(date: string, now: Date): string {
  const d = new Date(`${date}T12:00:00-05:00`);
  const yesterday = chicago(new Date(now.getTime() - 86_400_000)).date;
  const named = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "America/Chicago" });
  return date === yesterday ? `Yesterday, ${named}` : named;
}

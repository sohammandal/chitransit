// Polls the live feed (and, while a stop is open, every stop's own wait). The poller rewrites
// both after each 30 s sweep, so the next fetch is timed to just after the next sweep lands
// rather than a blind interval; a failed or overdue feed retries calmly (never a tight loop),
// and polling pauses while the tab is hidden.
import type { Feed, StopWaits } from "./types";

const SWEEP_MS = 30_000;
const RETRY_MS = 10_000;
// When a new file can be fetched: the CDN served each new file 3.9-5.2 s (p50 4.3 s) after its
// generated_at (11 sweeps, Oct 4). Asking 2 s after the next sweep's time, as before, nearly
// always got the old copy and then waited 10 s (shown ~12 s after it was made); asking at 5.5 s
// gets it first time, else 1.5 s later (shown ~6 s after).
const LANDS_MS = 5_500;
const STALE_RETRY_MS = 1_500;
const STALE_FOR_MS = 15_000; // past this, the poller is late: back to the calm retry

export const pollFeed = (onFeed: (feed: Feed) => void, onError: () => void) =>
  poll<Feed>("/live/vehicles.json", onFeed, onError);

export const pollStopWaits = (onWaits: (waits: StopWaits) => void) =>
  poll<StopWaits>("/live/stops.json", onWaits, () => {}); // none yet: the stop view says nothing

function poll<T extends { generated_at: string }>(url: string, onData: (data: T) => void, onError: () => void): () => void {
  let timer = 0;
  let last: number | null = null;

  async function poll() {
    let ok = false;
    let fresh = false;
    try {
      const r = await fetch(url, { cache: "no-cache" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: T = await r.json();
      const at = new Date(data.generated_at).getTime();
      ok = true;
      fresh = at !== last;
      last = at;
      if (fresh) onData(data); // the old copy again: nothing new to show
    } catch {
      onError();
    }
    timer = window.setTimeout(poll, nextFetch(last, ok, fresh, Date.now()) + Math.random() * 500);
  }

  const onVisibility = () => {
    window.clearTimeout(timer);
    if (document.visibilityState === "visible") poll();
  };
  document.addEventListener("visibilitychange", onVisibility);
  poll();
  return () => {
    window.clearTimeout(timer);
    document.removeEventListener("visibilitychange", onVisibility);
  };
}

/** ms until the next fetch, given the newest file's time (`last`), whether this fetch worked and
 *  brought a new file: just after the next file lands; soon again while it's still the old one. */
export function nextFetch(last: number | null, ok: boolean, fresh: boolean, now: number): number {
  if (!ok || last == null) return RETRY_MS;
  const due = last + SWEEP_MS + LANDS_MS - now;
  if (due > 0) return Math.min(SWEEP_MS + LANDS_MS, due);
  if (!fresh && -due < STALE_FOR_MS) return STALE_RETRY_MS;
  return RETRY_MS;
}

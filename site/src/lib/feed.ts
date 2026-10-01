// Polls the live feed (and, while a stop is open, every stop's own wait). The poller rewrites
// both after each 30 s sweep, so the next fetch is timed to just after the next sweep lands
// rather than a blind interval; a failed or overdue feed retries calmly (never a tight loop),
// and polling pauses while the tab is hidden.
import type { Feed, StopWaits } from "./types";

const SWEEP_MS = 30_000;
const RETRY_MS = 10_000;

export const pollFeed = (onFeed: (feed: Feed) => void, onError: () => void) =>
  poll<Feed>("/live/vehicles.json", onFeed, onError);

export const pollStopWaits = (onWaits: (waits: StopWaits) => void) =>
  poll<StopWaits>("/live/stops.json", onWaits, () => {}); // none yet: the stop view says nothing

function poll<T extends { generated_at: string }>(url: string, onData: (data: T) => void, onError: () => void): () => void {
  let timer = 0;
  let last: number | null = null;

  async function poll() {
    let ok = false;
    try {
      const r = await fetch(url, { cache: "no-cache" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: T = await r.json();
      last = new Date(data.generated_at).getTime();
      ok = true;
      onData(data);
    } catch {
      onError();
    }
    const due = last ? last + SWEEP_MS + 2_000 - Date.now() : RETRY_MS;
    const wait = !ok || due < 2_000 ? RETRY_MS : Math.min(SWEEP_MS + 5_000, due);
    timer = window.setTimeout(poll, wait + Math.random() * 1_500);
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

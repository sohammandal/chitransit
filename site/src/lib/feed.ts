// Polls the live feed. The poller rewrites it after each 30 s sweep, so the next fetch is
// timed to just after the next sweep lands rather than a blind interval; a failed or overdue
// feed retries calmly (never a tight loop), and polling pauses while the tab is hidden.
import type { Feed } from "./types";

const URL = "/live/vehicles.json";
const SWEEP_MS = 30_000;
const RETRY_MS = 10_000;

export function pollFeed(onFeed: (feed: Feed) => void, onError: () => void): () => void {
  let timer = 0;
  let last: number | null = null;

  async function poll() {
    let ok = false;
    try {
      const r = await fetch(URL, { cache: "no-cache" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const feed: Feed = await r.json();
      last = new Date(feed.generated_at).getTime();
      ok = true;
      onFeed(feed);
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

<script lang="ts">
  import Mark from "./Mark.svelte";

  let { count, age, failed, onRoute }: {
    count: number | null;
    age: number | null; // seconds since the feed was written
    failed: boolean;
    onRoute: (rt: string) => boolean; // false if no such route is running
  } = $props();

  let query = $state("");
  let missing = $state(false);

  function submit(e: SubmitEvent) {
    e.preventDefault();
    const rt = query.trim().toUpperCase();
    if (!rt) return;
    missing = !onRoute(rt);
    if (!missing) (document.activeElement as HTMLElement | null)?.blur(); // phones: close the keyboard
  }
</script>

<header class="bar">
  <a class="brand" href="/" aria-label="chitransit.fyi home">
    <Mark />
    <span><span class="chi">chi</span>transit<b>.fyi</b></span>
  </a>
  <p class="status" aria-live="polite">
    {#if count === null}
      {failed ? "Can't reach the live feed. Retrying." : "Connecting…"}
    {:else}
      <span class="dot" class:stale={failed}></span>
      <span><strong>{count}</strong> buses on the road</span>
      <span class="updated">{failed ? "Reconnecting…" : `Updated ${age ?? 0} s ago`}</span>
    {/if}
  </p>
  <form onsubmit={submit}>
    <label class="sr" for="route-q">Go to a route</label>
    <input
      id="route-q"
      bind:value={query}
      oninput={() => (missing = false)}
      placeholder="Route, e.g. 66"
      autocomplete="off"
      autocapitalize="characters"
      spellcheck="false"
      enterkeyhint="go"
    />
    {#if missing}<p class="miss">No #{query.trim().toUpperCase()} buses on the road right now.</p>{/if}
  </form>
</header>

<style>
  .bar {
    background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
    padding: 12px 14px; display: grid; gap: 8px;
  }
  .brand { display: flex; align-items: center; gap: 8px; color: var(--asphalt); text-decoration: none; font-size: 19px; font-weight: 800; letter-spacing: -0.01em; }
  .brand b { font-weight: 400; color: var(--fog); }
  .chi { color: var(--flag-blue); }
  .status { margin: 0; font-size: 15px; display: grid; grid-template-columns: auto 1fr; column-gap: 8px; align-items: baseline; }
  .status strong { font-size: 17px; font-weight: 800; }
  .updated { grid-column: 2; color: var(--fog); font-size: 13px; }
  .dot { align-self: center; width: 8px; height: 8px; border-radius: 50%; background: var(--flag-blue); animation: pulse 2s infinite; }
  .dot.stale { background: var(--signal-amber); }
  @keyframes pulse { 50% { opacity: 0.3; } }
  input {
    width: 100%; font: inherit; font-size: 16px; /* 16px: iOS won't zoom on focus */
    padding: 10px 12px; min-height: 44px; border-radius: 10px;
    border: 1.5px solid var(--rule); background: var(--concrete); color: var(--asphalt);
  }
  .miss { margin: 6px 2px 0; font-size: 13px; color: var(--fog); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>

<script lang="ts">
  // Brand, live status and route search. Search suggests as you type and opens a route once
  // typing pauses (lib/search.ts), so phones need no Return key; a tap on a suggestion or
  // Return opens at once.
  import { untrack } from "svelte";
  import { matchRoutes, toOpen } from "../lib/search";
  import Mark from "./Mark.svelte";

  let { route, count, age, failed, names, onOpen, aboutOpen = false, onAbout }: {
    count: number | null;
    age: number | null; // seconds since the feed was written
    failed: boolean;
    names: Record<string, string>;
    route: string | null; // the open route: leaving one this search opened clears the box
    onOpen: (rt: string, replace: boolean) => void; // replace: swap the history entry
    aboutOpen?: boolean;
    onAbout: () => void;
  } = $props();

  const PAUSE_MS = 400;

  let query = $state("");
  let focused = $state(false);
  let opened = $state<string | null>(null); // the route this search already opened
  let timer = 0;

  const matches = $derived(matchRoutes(query, names));
  const showList = $derived(focused && query.trim() !== "" && matches.length > 0 && matches[0].rt !== opened);

  $effect(() => {
    if (route !== untrack(() => opened)) [query, opened] = ["", null];
  });

  function open(rt: string, done: boolean) {
    if (rt !== opened) onOpen(rt, opened !== null);
    opened = rt;
    if (done) {
      query = "";
      opened = null;
      (document.activeElement as HTMLElement | null)?.blur(); // phones: close the keyboard
    }
  }

  function typed() {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const rt = toOpen(query, names);
      if (rt) open(rt, false);
    }, PAUSE_MS);
  }

  function submit(e: SubmitEvent) {
    e.preventDefault();
    window.clearTimeout(timer);
    const rt = toOpen(query, names) ?? matches[0]?.rt;
    if (rt) open(rt, true);
  }
</script>

<header class="bar">
  <a class="brand" href="/" aria-label="chitransit.fyi home">
    <Mark />
    <span><span class="chi">chi</span>transit<b>.fyi</b></span>
  </a>
  <button class="about-btn" onclick={onAbout} aria-expanded={aboutOpen}>About</button>
  <!-- Two lines in every state, so nothing moves when the first feed arrives. -->
  <p class="status" aria-live="polite">
    {#if count === null}
      <span class="dot waiting" class:stale={failed}></span>
      <span>{failed ? "Can't reach the live feed" : "Connecting to the live feed…"}</span>
      <span class="updated">{failed ? "Retrying…" : "\u00a0"}</span>
    {:else}
      <span class="dot" class:stale={failed}></span>
      <span><strong>{count}</strong> buses on the road</span>
      <span class="updated">{failed ? "Reconnecting…" : `Updated ${age ?? 0} s ago`}</span>
    {/if}
  </p>
  <form onsubmit={submit} role="search">
    <label class="sr" for="route-q">Go to a route</label>
    <input
      id="route-q"
      type="search"
      role="combobox"
      name="route-search"
      bind:value={query}
      oninput={typed}
      onfocus={() => (focused = true)}
      onblur={() => setTimeout(() => { focused = false; opened = null; }, 150)}
      placeholder="Route number or name, e.g. 66"
      autocomplete="off"
      autocorrect="off"
      autocapitalize="characters"
      spellcheck="false"
      enterkeyhint="go"
      aria-controls="route-list"
      aria-expanded={showList}
    />
    {#if showList}
      <ul id="route-list" class="list">
        {#each matches as m (m.rt)}
          <li>
            <button type="button" onmousedown={(e) => e.preventDefault()} onclick={() => open(m.rt, true)}>
              <span class="rt">{m.rt}</span><span class="name">{m.name}</span>
            </button>
          </li>
        {/each}
      </ul>
    {:else if focused && query.trim() && !matches.length}
      <p class="miss">No route matches “{query.trim()}”</p>
    {/if}
  </form>
</header>

<style>
  .bar {
    position: relative; background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
    padding: 12px 14px; display: grid; gap: 8px;
  }
  .about-btn {
    position: absolute; top: 10px; right: 10px; height: 32px; padding: 4px 12px 0; /* Overpass sits high: nudge */
    display: inline-flex; align-items: center; line-height: 1;
    border: 1px solid var(--rule); border-radius: 999px; background: var(--surface);
    font: inherit; font-size: 13px; font-weight: 700; color: var(--fog); cursor: pointer;
  }
  .about-btn[aria-expanded="true"] { color: var(--asphalt); border-color: var(--asphalt); }
  .brand { display: flex; align-items: center; gap: 8px; color: var(--asphalt); text-decoration: none; font-size: 19px; font-weight: 800; letter-spacing: -0.01em; }
  .brand b { font-weight: 400; color: var(--fog); }
  .chi { color: var(--flag-blue); }
  .status { margin: 0; font-size: 15px; display: grid; grid-template-columns: auto 1fr; grid-template-rows: 25px auto; column-gap: 8px; align-items: baseline; }
  .status strong { font-size: 17px; font-weight: 800; }
  .updated { grid-column: 2; color: var(--fog); font-size: 13px; }
  /* Sits on the text's baseline, raised to the middle of its letters: capital-height digits
     when live, mostly lowercase words while connecting (measured at 4x). */
  .dot { align-self: baseline; position: relative; top: -1.5px; width: 8px; height: 8px; border-radius: 50%; background: var(--flag-blue); animation: pulse 2s infinite; }
  .dot.stale { background: var(--signal-amber); }
  .dot.waiting { top: -1px; }
  @keyframes pulse { 50% { opacity: 0.3; } }
  /* A search field (not text) so iOS is less inclined to offer contact autofill; the search
     type's own look is reset. */
  input {
    appearance: none; -webkit-appearance: none;
    width: 100%; font: inherit; font-size: 16px; /* 16px: iOS won't zoom on focus */
    padding: 10px 12px; min-height: 44px; border-radius: 10px;
    border: 1.5px solid var(--rule); background: var(--concrete); color: var(--asphalt);
  }
  input::-webkit-search-decoration, input::-webkit-search-cancel-button { -webkit-appearance: none; appearance: none; }
  /* Suggestions float over the map: the bar keeps its height, so the map's fit doesn't shift. */
  form { position: relative; }
  .list, .miss {
    position: absolute; z-index: 5; left: 0; right: 0; top: calc(100% + 6px);
    background: var(--surface); border-radius: 12px; box-shadow: var(--shadow);
  }
  .list { list-style: none; margin: 0; padding: 4px; display: grid; gap: 2px; }
  .list button {
    width: 100%; display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 6px 8px;
    border: 0; border-radius: 10px; background: none; font: inherit; color: inherit; text-align: left; cursor: pointer;
  }
  .list button:hover, .list button:focus-visible { background: var(--concrete); }
  .rt { display: inline-grid; place-items: center; min-width: 44px; height: 28px; padding: 4px 8px 0; /* Overpass numerals sit high: nudge */ border-radius: 8px; background: var(--asphalt); color: var(--surface); font-weight: 900; font-size: 14px; flex: none; }
  .name { font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .miss { margin: 0; padding: 12px 14px; font-size: 13px; color: var(--fog); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>

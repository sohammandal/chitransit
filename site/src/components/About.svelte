<script lang="ts">
  import Mark from "./Mark.svelte";
  // onClose: the app's panel; without it, the text alone (pages/about.astro's copy in the HTML)
  let { onClose }: { onClose?: () => void } = $props();
</script>

<div class="sheet-head">
<header>
  <h2>Every CTA bus and train, live</h2>
  <span class="tag">Unofficial</span>
  {#if onClose}<button class="x" onclick={onClose} aria-label="Close">×</button>{/if}
</header>
</div>
<p class="what">How Chicago's buses and trains are really running, live and over time</p>
<dl>
  <dt>Search a route or line</dt>
  <dd>Each direction as a line, with its gaps</dd>
  <dt>Tap any bus or train</dt>
  <dd>On the map, or on a line</dd>
  <dt>Zoom in for stops and stations</dt>
  <dd>Tap one to see what's coming</dd>
  <dt>Right now</dt>
  <dd>Long waits and bunching, judged against the schedule</dd>
  <dt>Live</dt>
  <dd>Buses every 30 seconds, trains every 20 (placed by CTA's track sensors, not GPS). A position can be up to a minute old</dd>
</dl>
<!-- as the reports' footer: the name, where else to go, then the small print -->
<footer class="fine">
  <span class="brand"><Mark /><span><span class="chi">chi</span>transit<b>.fyi</b></span></span>
  <nav aria-label="More from chitransit.fyi">
    <a href="/reports/marathon-2026/">Marathon report</a>
    <a target="_blank" rel="noopener" href="https://github.com/sohammandal/chitransit">Open source</a>
    <a target="_blank" rel="noopener" href="https://github.com/sohammandal/chitransit/issues">Feedback</a>
  </nav>
  <p>Data provided by Chicago Transit Authority. Official info: <a target="_blank" rel="noopener" href="https://www.transitchicago.com">transitchicago.com</a></p>
  <p>chitransit.fyi is an independent project. It is not affiliated with, made by, or endorsed by the CTA. Figures are our own estimates, and data may be delayed or incomplete</p>
  <p>Privacy: no cookies, accounts or ads. Page views are counted without storing who you are, and "locate me" stays on your device</p>
</footer>

<style>
  header { display: flex; align-items: center; gap: 10px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; }
  .tag { display: inline-flex; align-items: center; height: 24px; padding: 0 10px; border: 1px solid var(--rule); border-radius: 999px; font-size: 12px; font-weight: 600; line-height: 1; color: var(--fog); padding-top: 4px; /* Overpass sits high: nudge */
    position: relative; top: -3px; /* centered on the heading's capitals, which sit 3 px above its line box's middle (measured Oct 4) */ }
  .x { margin-left: auto; width: 44px; height: 44px; flex: none; border: 0; border-radius: 50%; background: var(--concrete); color: var(--fog); font-size: 24px; line-height: 1; cursor: pointer; }
  dl { margin: 0; display: grid; gap: 2px; font-size: 15px; } /* each line under its heading */
  dt { font-weight: 700; }
  dt:not(:first-child) { margin-top: 12px; }
  dd { margin: 0; color: var(--fog); }
  .what { margin: 6px 0 0; font-size: 16px; line-height: 1.4; }
  .fine { border-top: 1px solid var(--rule); padding-top: 18px; display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; font-size: 13px; color: var(--fog); line-height: 1.45; }
  .fine p { margin: 0; }
  .brand { display: flex; align-items: center; gap: 8px; color: var(--asphalt); font-size: 18px; font-weight: 800; letter-spacing: -0.01em; }
  .brand b { font-weight: 400; color: var(--fog); }
  .chi { color: var(--flag-blue); }
  /* a pill on hover with room around the text, as the reports' footer */
  nav { display: flex; flex-wrap: wrap; justify-content: center; gap: 0 2px; margin-bottom: 2px; }
  nav a { padding: 12.75px 12px 9.25px; /* text centred: Overpass sits high */ border-radius: 999px; color: var(--asphalt); font-weight: 600; font-size: 14px; text-decoration: none; }
  nav a:hover { background: var(--surface2); }
</style>

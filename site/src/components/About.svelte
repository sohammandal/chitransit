<script lang="ts">
  let { onClose }: { onClose: () => void } = $props();
</script>

<div class="sheet-head">
<header>
  <h2>Every CTA bus, live</h2>
  <span class="tag">Unofficial</span>
  <button class="x" onclick={onClose} aria-label="Close">×</button>
</header>
</div>
<p class="what">How Chicago's buses are really running, live and over time</p>
<dl>
  <dt>Search a route</dt>
  <dd>Each direction as a line, with its gaps</dd>
  <dt>Tap any bus</dt>
  <dd>On the map, or on a route's line</dd>
  <dt>Zoom in for stops</dt>
  <dd>Tap one to see what's coming</dd>
  <dt>Right now</dt>
  <dd>Long waits and bunching, judged against the schedule</dd>
  <dt>Live</dt>
  <dd>Updates every 30 seconds. A position can be up to a minute old</dd>
</dl>
<div class="fine">
  <p>Data provided by Chicago Transit Authority</p>
  <p>chitransit.fyi is an independent project. It is not affiliated with, made by, or endorsed by the CTA. Figures are our own estimates, and data may be delayed or incomplete</p>
  <p>Official info: <a href="https://www.transitchicago.com">transitchicago.com</a></p>
</div>

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
  .what { margin: -4px 0 0; font-size: 16px; line-height: 1.4; }
  .fine { border-top: 1px solid var(--rule); padding-top: 12px; display: grid; gap: 6px; font-size: 13px; color: var(--fog); line-height: 1.45; }
  .fine p { margin: 0; }
</style>

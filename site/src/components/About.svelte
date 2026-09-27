<script lang="ts">
  let { onClose }: { onClose: () => void } = $props();
</script>

<header>
  <h2>Every CTA bus, live</h2>
  <span class="tag">Unofficial</span>
  <button class="x" onclick={onClose} aria-label="Close">×</button>
</header>
<dl>
  <dt>Updates</dt>
  <dd>Every 30 seconds. A position can be up to a minute old.</dd>
  <dt>Right now</dt>
  <dd>Long waits and buses arriving together, judged against each route's schedule for this hour.</dd>
  <dt>Colors</dt>
  <dd>On a route's line, <b class="amber">amber</b> buses are arriving together, bunched up, and <b class="red">red</b> marks a long wait: over twice the scheduled gap.</dd>
  <dt>Tap a route or bus</dt>
  <dd>See the route live or its report card, and when the last bus passed.</dd>
</dl>
<div class="fine">
  <p>Data provided by Chicago Transit Authority.</p>
  <p>chitransit.fyi is an independent project. It is not affiliated with, made by, or endorsed by the CTA. Figures are our own estimates, and data may be delayed or incomplete.</p>
  <p>Official info: <a href="https://www.transitchicago.com">transitchicago.com</a></p>
</div>

<style>
  header { display: flex; align-items: center; gap: 10px; }
  h2 { margin: 0; font-size: 20px; line-height: 1.15; }
  .tag { display: inline-flex; align-items: center; height: 24px; padding: 0 10px; border: 1px solid var(--rule); border-radius: 999px; font-size: 12px; font-weight: 600; line-height: 1; color: var(--fog); padding-top: 4px; /* Overpass sits high: nudge */ }
  .x { margin-left: auto; width: 44px; height: 44px; flex: none; border: 0; border-radius: 50%; background: var(--concrete); color: var(--fog); font-size: 24px; line-height: 1; cursor: pointer; }
  dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 10px 16px; font-size: 15px; }
  dt { font-weight: 700; }
  dd { margin: 0; }
  .amber { color: var(--signal-amber); }
  .red { color: var(--star-red); }
  .fine { border-top: 1px solid var(--rule); padding-top: 12px; display: grid; gap: 6px; font-size: 13px; color: var(--fog); line-height: 1.45; }
  .fine p { margin: 0; }
</style>

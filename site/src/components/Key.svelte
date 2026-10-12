<script lang="ts">
  let { partial = false, square = false }: {
    partial?: boolean; // any short-turn bus on the lines
    square?: boolean; // trains: drawn as squares, together outlined
  } = $props();
</script>

<!-- What the colors on a route line mean. -->
<p class="key">
  <span><i class="dot" class:square></i>Together</span>
  <span><i class="bar"></i>Long wait, over twice the schedule</span>
  <!-- drawn as on the line: a blue ring (amber when together, as the dot above says) -->
  {#if partial}<span><i class="dot other"></i>Runs part of the route</span>{/if}
</p>

<style>
  .key { margin: 0; display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; color: var(--fog); }
  span { display: inline-flex; align-items: center; gap: 6px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--signal-amber); }
  .dot.square { border-radius: 3px; background: none; border: 3px solid var(--signal-amber); width: 12px; height: 12px; box-sizing: border-box; }
  .dot.other { background: none; border: 2px solid var(--flag-blue); width: 10px; height: 10px; box-sizing: border-box; }
  .bar { width: 18px; height: 4px; border-radius: 2px; background: var(--star-red); opacity: 0.7; }
</style>

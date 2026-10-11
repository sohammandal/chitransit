<!-- CTA has this route and direction on a detour (the feed's rerouted, lib/problems.ts isRerouted).
     Neutral and dashed, like the regular streets a reroute skips on the map: amber means buses
     together. Centred on the capitals of a label beside it (measured at 4x, Oct 10). -->
<script lang="ts">
  let { alone = false }: { alone?: boolean } = $props(); // alone: on a line of its own (no gap before it)
</script>

<span class="tag" class:alone>Rerouted</span>

<style>
  .tag { margin-left: 8px; padding: 2px 6px 0; /* Overpass capitals sit high */ border: 1px dashed var(--fog); border-radius: 6px; color: var(--asphalt); font-size: 11px; font-weight: 700; letter-spacing: 0.02em; vertical-align: 1px; white-space: nowrap; }
  .tag.alone { margin: 3px 0 1px; justify-self: start; }
</style>

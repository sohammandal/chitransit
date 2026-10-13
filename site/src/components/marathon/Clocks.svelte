<script lang="ts">
  // Loads the per-route timeline (what CTA said, what its detour flags said, what the buses did);
  // its rows pick a route for the whole page (RouteTimeline.svelte).
  import { onMount } from "svelte";
  import RouteTimeline from "./RouteTimeline.svelte";
  import { load, type Timeline } from "../../lib/marathon";

  let data = $state.raw<Timeline | null>(null);
  let failed = $state(false);
  onMount(() => { load<Timeline>("timeline.json").then((d) => (data = d), () => (failed = true)); });
</script>

<div class="slot">
  {#if failed}
    <p class="fail">The chart's data didn't load. Reload the page to try again</p>
  {:else if data}
    <RouteTimeline {data} />
  {/if}
</div>

<style>
  .slot { min-height: 850px; } /* the chart's own height: nothing below jumps when it draws */
  .fail { color: var(--fog); }
</style>

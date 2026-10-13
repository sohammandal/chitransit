<script lang="ts">
  // The wall: buses crossing the course at street level, per 15 minutes, against normal Sundays,
  // and the crossings that went under it on expressways.
  import { onMount } from "svelte";
  import TimeChart from "./TimeChart.svelte";
  import { load, type CrossingSeries } from "../../lib/marathon";

  let data = $state.raw<CrossingSeries | null>(null);
  let failed = $state(false);
  onMount(() => { load<CrossingSeries>("crossings.json").then((d) => (data = d), () => (failed = true)); });
</script>

<div class="slot">
  {#if failed}
    <p class="fail">The chart's data didn't load. Reload the page to try again</p>
  {:else if data}
    <TimeChart start={data.start} step={data.step} unit=""
      title="Buses crossing the Marathon course" yLabel="Crossings per 15 minutes" moments={data.events}
      lines={[
        { values: data.cross, label: "At street level", tone: "red" },
        { values: data.under, label: "Under it, on an expressway", tone: "amber" },
      ]}
      band={{ lo: data.cross_band.map((b) => b[0]), hi: data.cross_band.map((b) => b[1]), label: "Normal Sundays, at street level" }}
      label="Buses crossing the Marathon course every 15 minutes: on normal Sundays about 120 to 190 crossings from 9 AM to 8 PM; on Marathon Sunday almost none at street level from 6:05 AM to 1:45 PM, while buses went under it on two expressways" />
  {:else}
    <div class="wait"></div>
  {/if}
</div>

<style>
  /* the chart's height once drawn, held while its data loads */
  .wait { height: 367px; }
  @media (min-width: 720px) { .wait { height: 323px; } }
  .fail { color: var(--fog); }
</style>

<script lang="ts">
  // The headline chart: on the 34 rerouted routes, buses out held near normal all day while the
  // stop visits riders get fell by a quarter.
  import { onMount } from "svelte";
  import TimeChart from "./TimeChart.svelte";
  import { load, type Series } from "../../lib/marathon";

  let data = $state.raw<Series | null>(null);
  let failed = $state(false);
  onMount(() => { load<Series>("series.json").then((d) => (data = d), () => (failed = true)); });
</script>

<div class="slot">
{#if failed}
  <p class="fail">The chart's data didn't load. Reload the page to try again</p>
{:else if data}
  <TimeChart start={data.start} step={data.step} max={110} unit="%" ticks={[0, 25, 50, 75, 100]}
    title="Buses out vs. bus visits to the usual stops" yLabel="% of the two Sundays before (their average)" moments={data.events}
    lines={[
      { values: data.M.buses, label: "Buses out", tone: "blue" },
      { values: data.M.stops, label: "Bus visits to usual stops", tone: "red" },
    ]}
    band={{ lo: data.M.stops_band.map((b) => b?.[0] ?? null), hi: data.M.stops_band.map((b) => b?.[1] ?? null), label: "Visits on Sep 27 and Oct 4" }}
    label="On the 34 rerouted routes, buses out stayed near 100% of a normal Sunday all day, while bus visits to their usual stops fell to about 75% from 6:15 AM to 3:30 PM and were back to about normal by 7 PM" />
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

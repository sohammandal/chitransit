<script lang="ts">
  // Route level, history: how reliable the route has been on every day we watched. Shares are
  // of scheduled trips, so the three add up to 100% and "didn't come" is a floor: some of the
  // trips we couldn't judge may not have run either.
  import { dayLabel } from "../lib/format";
  import type { ReportRow } from "../lib/types";

  let { days, rows, now }: {
    days: string[];
    rows: ReportRow[] | undefined; // aligned with days
    now: Date;
  } = $props();

  const pct = (n: number, of: number) => (of ? `${((100 * n) / of).toFixed(1)}%` : "–");
  const total = $derived(watched.reduce<ReportRow>((t, d) => t.map((v, i) => v + d.r[i]) as ReportRow, [0, 0, 0, 0, 0]));
  const [scheduled, ran, announced, proven, unclear] = $derived(total);
  const missed = $derived(announced + proven);
  // A rolling window once there's enough history: "Since Sep 26" until then.
  const WINDOW = 30;
  const all = $derived((rows ?? []).map((r, i) => ({ day: days[i], r })).filter((d) => d.r[0] > 0));
  const watched = $derived(all.slice(-WINDOW).reverse()); // newest first
  const since = $derived(watched.length ? dayLabel(watched.at(-1)!.day, now).replace("Yesterday, ", "") : "");
</script>

{#if !scheduled}
  <p class="note">No report for this route yet: it had no scheduled trips on the days we've watched.</p>
{:else}
  <div class="head">
    <p class="since">{all.length > WINDOW ? `Last ${WINDOW} days` : `Since ${since}`}</p>
    <p class="meta">
      <span>{watched.length} {watched.length === 1 ? "day" : "days"} watched</span>
      <span>{scheduled.toLocaleString()} trips scheduled</span>
    </p>
  </div>

  <dl class="figs">
    <div><dt>Ran</dt><dd>{pct(ran, scheduled)}</dd></div>
    <div class="bad"><dt>Didn't come</dt><dd>{pct(missed, scheduled)}</dd></div>
    <div class="muted"><dt>Couldn't tell</dt><dd>{pct(unclear, scheduled)}</dd></div>
  </dl>

  <div class="bar" role="img" aria-label="{pct(ran, scheduled)} ran, at least {pct(missed, scheduled)} didn't come, {pct(unclear, scheduled)} couldn't tell">
    <i class="ran" style:flex-grow={ran}></i>
    <i class="missed" style:flex-grow={missed}></i>
    <i class="unclear" style:flex-grow={unclear}></i>
  </div>

  <p class="split">
    At least {missed} {missed === 1 ? "trip" : "trips"} didn't come: {announced} canceled with notice, {proven} never came with no notice.
  </p>

  {#if watched.length > 1}
    <table>
      <thead><tr><th>Day</th><th>Ran</th><th>Didn't come</th><th>Couldn't tell</th></tr></thead>
      <tbody>
        {#each watched as d (d.day)}
          <tr><td>{dayLabel(d.day, now)}</td><td>{pct(d.r[1], d.r[0])}</td><td>{d.r[2] + d.r[3]}</td><td>{d.r[4]}</td></tr>
        {/each}
      </tbody>
    </table>
  {/if}

  {#if watched.length < 7}
    <p class="note">Early days: this card adds a day every morning.</p>
  {/if}

  <dl class="fine">
    <dt>Ran</dt><dd>The bus ran it, or was seen just before and after.</dd>
    <dt>Didn't come</dt><dd>Canceled with notice, or its bus was seen but never on this trip.</dd>
    <dt>Couldn't tell</dt><dd>Not enough data to say. Some of these may not have run.</dd>
  </dl>
{/if}

<style>
  .head { display: grid; gap: 1px; }
  .since { margin: 0; font-size: 15px; font-weight: 800; }
  .meta { margin: 0; display: flex; flex-wrap: wrap; font-size: 13px; color: var(--fog); }
  .meta span + span::before { content: "·"; margin-inline: 8px; }
  .figs { margin: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .figs div { display: flex; flex-direction: column-reverse; justify-content: flex-end; gap: 2px; }
  .figs dt { font-size: 12px; color: var(--fog); }
  .figs dd { margin: 0; font-size: 24px; font-weight: 800; line-height: 1.1; letter-spacing: -0.01em; }
  .figs .bad dd { color: var(--star-red); }
  .figs .muted dd { color: var(--fog); }
  .bar { display: flex; gap: 2px; height: 10px; border-radius: 5px; overflow: hidden; }
  .bar i { min-width: 0; flex-basis: 0; }
  .bar i[style*="flex-grow: 0"] { display: none; }
  .ran { background: var(--flag-blue); }
  .missed { background: var(--star-red); }
  .unclear { background: repeating-linear-gradient(135deg, var(--rule) 0 3px, var(--concrete) 3px 6px); }
  .split { margin: 0; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; font-weight: 700; color: var(--fog); font-size: 12px; padding: 4px 0; border-bottom: 1px solid var(--rule); }
  td { padding: 5px 0; border-bottom: 1px solid var(--rule); }
  th:not(:first-child), td:not(:first-child) { text-align: right; }
  .note { margin: 0; font-size: 12px; color: var(--fog); }
  .fine { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 4px 10px; font-size: 12px; line-height: 1.4; color: var(--fog); }
  .fine dt { font-weight: 700; color: var(--asphalt); }
  .fine dd { margin: 0; }
</style>

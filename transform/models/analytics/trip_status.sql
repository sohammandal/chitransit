-- One row per scheduled bus trip and service date. Two questions, then one status:
--   did it run?        ran / likely ran / not seen / not watched
--   was it announced?  CTA flagged it canceled (dyn = 1) in a sampled prediction, or not
-- status (first rule that applies):
--   pending    : not over yet (scheduled end + 30 min is after the run's as_of)
--   ran        : an observed trip explains it (int_trip_matches) with at least 2 reports
--   flagged    : not ran, and CTA announced it canceled at a stop it serves
--   unknown    : not watched (feed swept < 90% of the trip's minutes), or the route ran
--                detour patterns within an hour of it (reason says which)
--   likely_ran : matched with a single report; or not seen, but the block's bus was on this
--                trip's pattern under another key during it, or vanished and reappeared at
--                the trip's far end (it drove the trip with a dead tracker)
--   silent     : not seen, not announced, and CTA's predictions at its own stops were
--                sampled at least twice in the 30 minutes before it was due (stops that
--                have shown a cancellation before only)
--   missing    : the same, but its stops weren't sampled: announcement unknown
-- ghost_evidence (silent/missing only) grades them: strict = the block's bus was seen doing
-- something else during the trip, or reappeared at the end it started from. gap_ratio
-- measures the hole riders felt; it is impact, not evidence (a bus with a dead tracker
-- leaves the same hole in our data).
with scheduled as (
    select * from {{ ref('int_scheduled_trips') }}
),

matched as (
    select * from {{ ref('int_trip_matches') }}
),

flags as (
    -- a flag belongs to a trip only at a stop that trip serves (GTFS), same route and start,
    -- and counts only if CTA didn't clear it later at that stop
    select
        s.service_date,
        s.trip_id,
        min(f.first_flagged_at) as first_flagged_at,
        sum(f.n_flagged_samples) as n_flagged_samples
    from scheduled as s
    inner join {{ ref('int_trip_flags') }} as f
        on
            s.service_date = f.service_date and s.rt = f.rt
            and s.start_s - f.start_s between 0 and {{ var('rule_start_tolerance_s') }}
    inner join {{ ref('int_scheduled_departures') }} as d
        on s.service_date = d.service_date and s.trip_id = d.trip_id and f.stop_id = d.stop_id
    where f.cleared_at is null
    group by all
),

watching as (
    select
        s.service_date,
        s.trip_id,
        count(*) filter (where m.swept)::double
        / (date_diff('minute', date_trunc('minute', s.scheduled_start), s.scheduled_end) + 1)
            as sweep_coverage
    from scheduled as s
    left join {{ ref('int_feed_minutes') }} as m
        on m.minute between date_trunc('minute', s.scheduled_start) and s.scheduled_end
    group by s.service_date, s.trip_id, s.scheduled_start, s.scheduled_end
),

flag_stops as (
    -- stops that have ever shown a cancellation (all history up to as_of, not just this window)
    select distinct stpid as stop_id
    from {{ ref('stg_predictions') }}
    where dyn = 1 and fetched_at <= {{ as_of() }}
),

sampled as (
    -- looks at the trip's own stops (GTFS) in the window before it was due there, counting
    -- only stops that have shown a cancellation at some point (CTA omits canceled
    -- predictions where a stop isn't configured to show them). A canceled trip is missing
    -- from ~27% of looks (Sep 26), so one look isn't enough to call it unannounced.
    select d.service_date, d.trip_id, count(distinct ps.poll_id) as n_flag_looks
    from {{ ref('int_scheduled_departures') }} as d
    inner join {{ ref('stg_prediction_samples') }} as ps
        on
            d.stop_id = ps.stop_id
            and ps.fetched_at >= d.departs_at - interval ({{ var('rule_flag_window_min') }}) minute
            and ps.fetched_at < d.departs_at
    where d.stop_id in (select stop_id from flag_stops)
    group by all
),

detoured as (
    select distinct s.service_date, s.trip_id
    from scheduled as s
    inner join {{ ref('int_detour_windows') }} as d
        on
            s.service_date = d.service_date and s.rt = d.rt
            and s.scheduled_start < d.window_end and s.scheduled_end > d.window_start
),

classified as (
    select
        s.*,
        m.matched_on,
        m.observed_pid,
        m.n_reports,
        m.first_seen_at,
        m.first_vid as vid,
        f.trip_id is not null as flagged,
        f.first_flagged_at,
        f.n_flagged_samples,
        w.sweep_coverage,
        coalesce(sp.n_flag_looks, 0) as n_flag_looks,
        coalesce(sp.n_flag_looks, 0) >= {{ var('rule_min_flag_looks') }} as flags_sampled,
        dt.trip_id is not null as detour,
        e.evidence as ghost_evidence,
        e.block_vid,
        g.gap_ratio
    from scheduled as s
    left join matched as m on s.service_date = m.service_date and s.trip_id = m.trip_id
    left join flags as f on s.service_date = f.service_date and s.trip_id = f.trip_id
    left join watching as w on s.service_date = w.service_date and s.trip_id = w.trip_id
    left join sampled as sp on s.service_date = sp.service_date and s.trip_id = sp.trip_id
    left join detoured as dt on s.service_date = dt.service_date and s.trip_id = dt.trip_id
    left join {{ ref('int_missing_trip_evidence') }} as e
        on s.service_date = e.service_date and s.trip_id = e.trip_id
    left join {{ ref('int_trip_service_gaps') }} as g
        on s.service_date = g.service_date and s.trip_id = g.trip_id
),

decided as (
    select
        *,
        case
            when scheduled_end + interval 30 minute > {{ as_of() }} then 'pending'
            when n_reports >= 2 then 'ran'
            when flagged then 'flagged'
            when sweep_coverage < {{ var('rule_coverage_min') }} then 'unknown'
            when detour and n_reports is null then 'unknown'
            when n_reports = 1 then 'likely_ran'
            when ghost_evidence in ('bus_on_this_pattern', 'reappeared_far_end') then 'likely_ran'
            when flags_sampled then 'silent'
            else 'missing'
        end as status
    from classified
)

select
    service_date,
    trip_id,
    feed_id,
    rt,
    direction,
    block_id,
    pid,
    scheduled_start,
    scheduled_end,
    status,
    case
        when status = 'unknown' and sweep_coverage < {{ var('rule_coverage_min') }} then 'not_watched'
        when status = 'unknown' then 'detour'
        when status = 'likely_ran' and n_reports = 1 then 'single_report'
        when status = 'likely_ran' and ghost_evidence = 'bus_on_this_pattern' then 'miskeyed'
        when status = 'likely_ran' then 'tracker_gap'
        when status = 'missing' then 'flags_not_sampled'
    end as reason,
    case when status in ('silent', 'missing') then ghost_evidence end as ghost_evidence,
    -- strict: physical evidence the block's bus did not run it
    status in ('silent', 'missing')
    and coalesce(ghost_evidence in ('bus_elsewhere', 'reappeared_same_end'), false) as strict_evidence,
    matched_on,
    observed_pid,
    vid,
    block_vid,
    n_reports,
    first_seen_at,
    flagged,
    first_flagged_at,
    n_flagged_samples,
    sweep_coverage,
    flags_sampled,
    n_flag_looks,
    gap_ratio
from decided

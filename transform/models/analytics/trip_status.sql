-- One row per scheduled bus trip and service date. Two questions, then one status:
--   did it run?        ran / likely ran / missing / not watched
--   was it announced?  CTA flagged it canceled (dyn = 1) in a sampled prediction, or not
-- status (first rule that applies):
--   pending    : not over yet (scheduled end + 30 min is after the run's as_of)
--   ran        : an observed trip matched on pid and start (GTFS starts 0-120 s after the
--                bus's stst), or, failing that, on route and start (a detour pattern);
--                at least 2 reports
--   flagged    : not ran, and CTA announced it canceled
--   unknown    : not watched (feed swept < 90% of the trip's minutes), or the route ran
--                detour patterns within an hour of it (reason says which)
--   likely_ran : not seen, but the same bus ran the trips before and after it in its GTFS
--                block and riders saw no hole in service (a tracker gap), or matched with a
--                single report. A block neighbor with a hole is a dropped trip, not a gap.
--   silent     : not seen, not announced, and one of this trip's stops was sampled for
--                predictions in the 30 minutes before the trip was due there
--   missing    : not seen while watched, but its stops weren't sampled, so we can't say
--                whether CTA announced it
with scheduled as (
    select * from {{ ref('int_scheduled_trips') }}
),

observed as (
    select o.*, p.direction as observed_direction
    from {{ ref('int_observed_trips') }} as o
    left join {{ ref('stg_patterns') }} as p on o.pid = p.pid
),

by_pattern as (
    select
        s.service_date,
        s.trip_id,
        o.pid as observed_pid,
        o.start_s as observed_start_s,
        o.n_reports,
        o.first_seen_at,
        o.last_seen_at,
        o.first_vid,
        o.last_vid
    from scheduled as s
    inner join observed as o
        on
            s.service_date = o.service_date and s.rt = o.rt and s.pid = o.pid
            and s.start_s - o.start_s between 0 and 120
    qualify
        row_number() over (partition by s.service_date, s.trip_id order by s.start_s - o.start_s) = 1
        and row_number() over (
            partition by o.service_date, o.rt, o.pid, o.start_s order by s.start_s - o.start_s
        ) = 1
),

by_route as (
    -- Leftovers on both sides: buses on patterns GTFS doesn't have (detours).
    select
        s.service_date,
        s.trip_id,
        o.pid as observed_pid,
        o.start_s as observed_start_s,
        o.n_reports,
        o.first_seen_at,
        o.last_seen_at,
        o.first_vid,
        o.last_vid
    from scheduled as s
    inner join observed as o
        on
            s.service_date = o.service_date and s.rt = o.rt
            and s.start_s - o.start_s between 0 and 120
    where
        not exists (
            select 1 from by_pattern as m
            where m.service_date = s.service_date and m.trip_id = s.trip_id
        )
        and not exists (
            select 1 from by_pattern as m
            where
                m.service_date = o.service_date and m.observed_pid = o.pid
                and m.observed_start_s = o.start_s
        )
    qualify
        row_number() over (
            partition by s.service_date, s.trip_id
            order by (o.observed_direction = s.direction) desc, s.start_s - o.start_s
        ) = 1
        and row_number() over (
            partition by o.service_date, o.rt, o.pid, o.start_s
            order by (o.observed_direction = s.direction) desc, s.start_s - o.start_s
        ) = 1
),

matched as (
    select *, 'pattern' as matched_on from by_pattern
    union all
    select *, 'route' as matched_on from by_route
),

flags as (
    -- a flag belongs to a trip only at a stop that trip serves (GTFS), same route and start
    select
        s.service_date,
        s.trip_id,
        min(f.first_flagged_at) as first_flagged_at,
        sum(f.n_flagged_samples) as n_flagged_samples
    from scheduled as s
    inner join {{ ref('int_trip_flags') }} as f
        on
            s.service_date = f.service_date and s.rt = f.rt
            and s.start_s - f.start_s between 0 and 120
    inner join {{ ref('int_scheduled_departures') }} as d
        on s.service_date = d.service_date and s.trip_id = d.trip_id and f.stop_id = d.stop_id
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

sampled as (
    -- one of the trip's own stops (GTFS) was sampled in the 30 minutes before the trip was
    -- due there: a flag would have shown up in that response
    select distinct d.service_date, d.trip_id
    from {{ ref('int_scheduled_departures') }} as d
    inner join {{ ref('stg_prediction_samples') }} as ps
        on
            d.stop_id = ps.stop_id
            and ps.fetched_at >= d.departs_at - interval 30 minute
            and ps.fetched_at < d.departs_at
),

block_neighbors as (
    -- the trips before and after this one in its block (one vehicle's run of trips)
    select
        s.service_date,
        s.trip_id,
        lag(m.n_reports >= 2) over blk as prev_ran,
        lag(m.last_vid) over blk as prev_vid,
        lead(m.n_reports >= 2) over blk as next_ran,
        lead(m.first_vid) over blk as next_vid
    from scheduled as s
    left join matched as m on s.service_date = m.service_date and s.trip_id = m.trip_id
    window blk as (partition by s.service_date, s.feed_id, s.block_id order by s.start_s)
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
        sp.trip_id is not null as flags_sampled,
        coalesce(b.prev_ran and b.next_ran and b.prev_vid = b.next_vid, false) as tracker_gap,
        dt.trip_id is not null as detour
    from scheduled as s
    left join matched as m on s.service_date = m.service_date and s.trip_id = m.trip_id
    left join flags as f on s.service_date = f.service_date and s.trip_id = f.trip_id
    left join watching as w on s.service_date = w.service_date and s.trip_id = w.trip_id
    left join sampled as sp on s.service_date = sp.service_date and s.trip_id = sp.trip_id
    left join block_neighbors as b on s.service_date = b.service_date and s.trip_id = b.trip_id
    left join detoured as dt on s.service_date = dt.service_date and s.trip_id = dt.trip_id
),

gaps as (
    select * from {{ ref('int_trip_service_gaps') }}
),

decided as (
    select
        c.*,
        g.gap_ratio,
        coalesce(g.gap_ratio >= 1.5, false) as gap_confirmed,
        -- same bus before and after, and no hole riders would have felt
        c.tracker_gap and not coalesce(g.gap_ratio >= 1.5, false) as ran_unseen
    from classified as c
    left join gaps as g on c.service_date = g.service_date and c.trip_id = g.trip_id
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
    case
        when scheduled_end + interval 30 minute > {{ as_of() }} then 'pending'
        when n_reports >= 2 then 'ran'
        when flagged then 'flagged'
        when sweep_coverage < 0.9 then 'unknown'
        when detour and n_reports is null then 'unknown'
        when ran_unseen or n_reports = 1 then 'likely_ran'
        when flags_sampled then 'silent'
        else 'missing'
    end as status,
    case
        when scheduled_end + interval 30 minute > {{ as_of() }} or n_reports >= 2 or flagged then null
        when sweep_coverage < 0.9 then 'not_watched'
        when detour and n_reports is null then 'detour'
        when n_reports = 1 then 'single_report'
        when ran_unseen then 'tracker_gap'
        when not flags_sampled then 'flags_not_sampled'
    end as reason,
    matched_on,
    observed_pid,
    vid,
    n_reports,
    first_seen_at,
    flagged,
    first_flagged_at,
    n_flagged_samples,
    sweep_coverage,
    flags_sampled,
    gap_ratio,
    -- corroboration: other buses' stop passes show the hole a missing trip leaves
    gap_confirmed
from decided

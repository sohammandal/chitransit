-- One row per scheduled bus trip and service date: ran, flagged (CTA marked it canceled,
-- dyn = 1), silent (neither seen nor flagged while we were watching), or unknown.
--   ran     : an observed trip matched on pid and start (GTFS starts 0-120 s after the bus's
--             stst), or, failing that, on route and start (a bus running a detour pattern);
--             at least 2 reports
--   flagged : not ran, and a sampled prediction flagged it
--   silent  : not ran, not flagged, the feed was swept for >= 90% of the trip's minutes and
--             predictions were sampled in the 30 minutes before it was due
--   unknown : anything else (we weren't looking closely enough to say)
--   pending : not over yet (scheduled end + 30 min is after the run's as_of)
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
        o.last_seen_at
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
        o.last_seen_at
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
    select
        s.service_date,
        s.trip_id,
        f.first_flagged_at,
        f.n_flagged_samples
    from scheduled as s
    inner join {{ ref('int_trip_flags') }} as f
        on
            s.service_date = f.service_date and s.rt = f.rt
            and s.start_s - f.start_s between 0 and 120
    qualify row_number() over (
        partition by s.service_date, s.trip_id
        order by (f.direction = s.direction) desc, s.start_s - f.start_s
    ) = 1
),

watching as (
    select
        s.service_date,
        s.trip_id,
        count(*) filter (
            where m.swept and m.minute >= date_trunc('minute', s.scheduled_start)
        )::double
        / (date_diff('minute', date_trunc('minute', s.scheduled_start), s.scheduled_end) + 1)
            as sweep_coverage,
        coalesce(
            bool_or(m.predictions_sampled) filter (where m.minute < s.scheduled_start), false
        ) as flags_sampled
    from scheduled as s
    left join {{ ref('int_feed_minutes') }} as m
        on m.minute between date_trunc('minute', s.scheduled_start) - interval 30 minute and s.scheduled_end
    group by s.service_date, s.trip_id, s.scheduled_start, s.scheduled_end
)

select
    s.service_date,
    s.trip_id,
    s.feed_id,
    s.rt,
    s.direction,
    s.pid,
    s.scheduled_start,
    s.scheduled_end,
    case
        when s.scheduled_end + interval 30 minute > {{ as_of() }} then 'pending'
        when m.n_reports >= 2 then 'ran'
        when f.trip_id is not null then 'flagged'
        when m.trip_id is null and w.sweep_coverage >= 0.9 and w.flags_sampled then 'silent'
        else 'unknown'
    end as status,
    m.matched_on,
    m.observed_pid,
    m.n_reports,
    m.first_seen_at,
    f.trip_id is not null as flagged,
    f.first_flagged_at,
    f.n_flagged_samples,
    w.sweep_coverage,
    w.flags_sampled
from scheduled as s
left join matched as m on s.service_date = m.service_date and s.trip_id = m.trip_id
left join flags as f on s.service_date = f.service_date and s.trip_id = f.trip_id
left join watching as w on s.service_date = w.service_date and s.trip_id = w.trip_id

-- Headways, gaps and bunching per stop, route, direction and hour (Chicago). A headway is
-- the time since the previous bus of the same route and direction passed the stop.
--   bunched: headway < 25% of the scheduled headway (Active Trans' definition)
--   gap:     headway > 2x the scheduled headway
-- Counted only in hours when the feed was swept >= 90% of minutes (else NULL): a feed
-- outage must not read as a service gap. Terminals (a pattern's first and last stop) are
-- left out: departures there are often unobservable (a bus starts reporting after it
-- leaves) and headways there are set by layovers.

-- hour_start is the clock hour (Chicago); trips after midnight keep their service_date.
-- Grain: (service_date, rt, direction, stop_id, hour_start).
with pattern_ends as (
    select pid, valid_from, valid_to, min(seq) as first_seq, max(seq) as last_seq
    from {{ ref('stg_pattern_stops') }}
    group by all
),

passes as (
    select
        p.service_date,
        p.rt,
        pt.direction,
        p.stop_id,
        p.passed_at,
        date_diff('second', lag(p.passed_at) over stop_line, p.passed_at) / 60.0 as headway_min
    from {{ ref('int_stop_passes') }} as p
    inner join {{ ref('stg_patterns') }} as pt on p.pid = pt.pid
    inner join pattern_ends as e
        on
            p.pid = e.pid and p.passed_at >= e.valid_from
            and (e.valid_to is null or p.passed_at < e.valid_to)
    where p.seq not in (e.first_seq, e.last_seq)
    window stop_line as (partition by p.service_date, p.rt, pt.direction, p.stop_id order by p.passed_at)
),

scheduled as (
    select
        service_date,
        rt,
        direction,
        stop_id,
        date_trunc('hour', departs_at) as hour_start,
        count(*) as scheduled_buses
    from {{ ref('int_scheduled_departures') }}
    where not is_terminal
    group by all
),

observed as (
    select
        service_date,
        rt,
        direction,
        stop_id,
        date_trunc('hour', passed_at) as hour_start,
        count(*) as observed_buses,
        median(headway_min) as median_headway_min,
        max(headway_min) as max_headway_min,
        list(headway_min) filter (where headway_min is not null) as headways
    from passes
    group by all
),

swept as (
    select date_trunc('hour', minute) as hour_start, count(*) filter (where swept) / 60.0 as sweep_coverage
    from {{ ref('int_feed_minutes') }}
    group by all
)

select
    coalesce(o.service_date, s.service_date) as service_date,
    coalesce(o.rt, s.rt) as rt,
    coalesce(o.direction, s.direction) as direction,
    coalesce(o.stop_id, s.stop_id) as stop_id,
    coalesce(o.hour_start, s.hour_start) as hour_start,
    coalesce(s.scheduled_buses, 0) as scheduled_buses,
    coalesce(o.observed_buses, 0) as observed_buses,
    round(60.0 / s.scheduled_buses, 1) as scheduled_headway_min,
    round(o.median_headway_min, 1) as median_headway_min,
    round(o.max_headway_min, 1) as max_headway_min,
    case when w.sweep_coverage >= {{ var('rule_coverage_min') }} and s.scheduled_buses > 0 then
        len(list_filter(o.headways, h -> h < 0.25 * 60.0 / s.scheduled_buses))
    end as bunched,
    case when w.sweep_coverage >= {{ var('rule_coverage_min') }} and s.scheduled_buses > 0 then
        len(list_filter(o.headways, h -> h > 2 * 60.0 / s.scheduled_buses))
    end as gaps,
    coalesce(w.sweep_coverage, 0) as sweep_coverage
from observed as o
full outer join scheduled as s
    on
        o.service_date = s.service_date and o.rt = s.rt and o.direction = s.direction
        and o.stop_id = s.stop_id and o.hour_start = s.hour_start
left join swept as w on w.hour_start = coalesce(o.hour_start, s.hour_start)

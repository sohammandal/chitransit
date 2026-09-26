-- For every scheduled trip no bus reported: physical evidence about the bus that should have
-- run it (the block's bus, from the trips before or after it in its GTFS block). Absence
-- from the feed alone can't tell a ghost from a bus driving with a dead tracker; where that
-- bus was can.
--   bus_on_this_pattern : the block's bus, or a bus CTA predicted for this trip (dyn = 0, at
--                         one of its stops), was on this trip's pattern during it under
--                         another trip key: it most likely ran it (mis-keyed)
--   bus_elsewhere       : visible during the trip's scheduled time, not on its pattern
--                         (still on an earlier trip, laid over, another route): it didn't run
--                         it, and its tracker worked. Strong ghost evidence.
--   reappeared_far_end  : invisible during the trip, next seen within 500 m of the trip's last
--                         stop: it drove the trip with a dead tracker
--   reappeared_same_end : invisible during the trip, next seen within 500 m of its first stop:
--                         it sat there. Strong ghost evidence.
--   reappeared_elsewhere, bus_not_seen_again, no_block_bus : no physical evidence either way
-- Grain: (service_date, trip_id), unmatched trips only.
with scheduled as (
    select * from {{ ref('int_scheduled_trips') }}
),

matches as (
    select * from {{ ref('int_trip_matches') }}
),

neighbors as (
    select
        s.service_date,
        s.trip_id,
        lag(m.n_reports >= 2) over blk as prev_ran,
        lag(m.last_vid) over blk as prev_vid,
        lead(m.n_reports >= 2) over blk as next_ran,
        lead(m.first_vid) over blk as next_vid,
        m.trip_id is not null as matched
    from scheduled as s
    left join matches as m on s.service_date = m.service_date and s.trip_id = m.trip_id
    window blk as (partition by s.service_date, s.feed_id, s.block_id order by s.start_s)
),

unseen as (
    select
        s.*,
        case when n.prev_ran then n.prev_vid when n.next_ran then n.next_vid end as block_vid
    from scheduled as s
    inner join neighbors as n on s.service_date = n.service_date and s.trip_id = n.trip_id
    where not n.matched
),

terminals as (
    select
        d.service_date,
        d.trip_id,
        arg_min(d.stop_id, d.stop_sequence) as first_stop_id,
        arg_max(d.stop_id, d.stop_sequence) as last_stop_id
    from {{ ref('int_scheduled_departures') }} as d
    group by all
),

stops as (
    select * from {{ ref('stg_gtfs_stops') }}
),

predicted_buses as (
    -- buses CTA's predictions assigned to this trip (not canceled) at a stop it serves
    select distinct u.service_date, u.trip_id, p.vid
    from unseen as u
    inner join {{ ref('stg_predictions') }} as p
        on
            u.rt = p.rt and p.dyn = 0 and p.vid is not null
            and (case when p.stst >= 86400 then p.stsd + 1 else p.stsd end) = u.service_date
            and u.start_s - (case when p.stst >= 86400 then p.stst - 86400 else p.stst end)
            between 0 and {{ var('rule_start_tolerance_s') }}
    inner join {{ ref('int_scheduled_departures') }} as d
        on u.service_date = d.service_date and u.trip_id = d.trip_id and p.stpid = d.stop_id
),

predicted_on_pattern as (
    select pb.service_date, pb.trip_id, count(*) as n
    from predicted_buses as pb
    inner join unseen as u on pb.service_date = u.service_date and pb.trip_id = u.trip_id
    inner join {{ ref('stg_bus_positions') }} as b
        on
            pb.vid = b.vid and b.rt = u.rt and b.pid = u.pid
            and b.observed_at between u.scheduled_start and u.scheduled_end
    group by all
),

during as (
    select
        u.service_date,
        u.trip_id,
        count(*) as n_seen,
        count(*) filter (where p.rt = u.rt and p.pid = u.pid) as n_on_this_pattern
    from unseen as u
    inner join {{ ref('stg_bus_positions') }} as p
        on p.vid = u.block_vid and p.observed_at between u.scheduled_start and u.scheduled_end
    group by all
),

after as (
    select
        u.service_date,
        u.trip_id,
        min(p.observed_at) as reappeared_at,
        arg_min(p.lat, p.observed_at) as lat,
        arg_min(p.lon, p.observed_at) as lon
    from unseen as u
    inner join {{ ref('stg_bus_positions') }} as p
        on
            p.vid = u.block_vid
            and p.observed_at > u.scheduled_end
            and p.observed_at <= u.scheduled_end + interval 2 hour
    group by all
),

measured as (
    select
        u.service_date,
        u.trip_id,
        u.block_vid,
        coalesce(d.n_seen, 0) as n_seen_during,
        coalesce(d.n_on_this_pattern, 0) + coalesce(pp.n, 0) as n_on_this_pattern,
        a.reappeared_at,
        -- equirectangular metres: plenty accurate across a city
        sqrt(
            power((a.lon - sf.lon) * 111320 * cos(radians(sf.lat)), 2)
            + power((a.lat - sf.lat) * 110540, 2)
        ) as m_from_first_stop,
        sqrt(
            power((a.lon - sl.lon) * 111320 * cos(radians(sl.lat)), 2)
            + power((a.lat - sl.lat) * 110540, 2)
        ) as m_from_last_stop
    from unseen as u
    left join during as d on u.service_date = d.service_date and u.trip_id = d.trip_id
    left join after as a on u.service_date = a.service_date and u.trip_id = a.trip_id
    left join predicted_on_pattern as pp on u.service_date = pp.service_date and u.trip_id = pp.trip_id
    left join terminals as t on u.service_date = t.service_date and u.trip_id = t.trip_id
    left join stops as sf on u.feed_id = sf.feed_id and t.first_stop_id = sf.stop_id
    left join stops as sl on u.feed_id = sl.feed_id and t.last_stop_id = sl.stop_id
)

select
    *,
    case
        when n_on_this_pattern > 0 then 'bus_on_this_pattern'
        when block_vid is null then 'no_block_bus'
        when n_seen_during > 0 then 'bus_elsewhere'
        when reappeared_at is null then 'bus_not_seen_again'
        when m_from_last_stop <= {{ var('rule_reappear_m') }} and m_from_last_stop < m_from_first_stop then 'reappeared_far_end'
        when m_from_first_stop <= {{ var('rule_reappear_m') }} then 'reappeared_same_end'
        else 'reappeared_elsewhere'
    end as evidence
from measured

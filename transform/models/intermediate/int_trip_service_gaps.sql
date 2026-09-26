-- Corroboration for a missing trip: at the trip's middle scheduled stop, how long riders
-- actually waited between the buses that passed just before and just after its scheduled
-- time, as a multiple of the scheduled headway there. A trip that really didn't run leaves
-- one bus missing from the sequence, so riders wait about 2x; trips that ran average ~1x.
-- Grain: (service_date, trip_id); NULL ratio when there were no passes on both sides.
with mid_stop as (
    select service_date, trip_id, rt, direction, stop_id, departs_at
    from (
        select
            *,
            row_number() over (partition by service_date, trip_id order by stop_sequence) as i,
            count(*) over (partition by service_date, trip_id) as n
        from {{ ref('int_scheduled_departures') }}
    )
    where i = n // 2 + 1
),

around as (
    select
        m.service_date,
        m.trip_id,
        max(p.passed_at) filter (where p.passed_at <= m.departs_at) as prev_pass,
        min(p.passed_at) filter (where p.passed_at > m.departs_at) as next_pass
    from mid_stop as m
    inner join {{ ref('int_stop_passes') }} as p
        on
            m.stop_id = p.stop_id and m.rt = p.rt
            and p.passed_at between m.departs_at - interval 2 hour and m.departs_at + interval 2 hour
    group by all
),

scheduled_headway as (
    -- mean scheduled gap at that stop, same route and direction, within an hour either side
    select
        m.service_date,
        m.trip_id,
        120.0 / nullif(count(*) - 1, 0) as headway_min
    from mid_stop as m
    inner join {{ ref('int_scheduled_departures') }} as d
        on
            m.stop_id = d.stop_id and m.rt = d.rt and m.direction = d.direction
            and m.service_date = d.service_date
            and d.departs_at between m.departs_at - interval 1 hour and m.departs_at + interval 1 hour
    group by all
)

select
    m.service_date,
    m.trip_id,
    m.stop_id as gap_stop_id,
    a.prev_pass,
    a.next_pass,
    h.headway_min as scheduled_headway_min,
    round(date_diff('second', a.prev_pass, a.next_pass) / 60.0 / h.headway_min, 2) as gap_ratio
from mid_stop as m
left join around as a on m.service_date = a.service_date and m.trip_id = a.trip_id
left join scheduled_headway as h on m.service_date = h.service_date and m.trip_id = h.trip_id

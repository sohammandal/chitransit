-- Every bus trip scheduled on each service date in the window, judged by the GTFS feed in
-- force that day (int_active_services).
select
    a.service_date,
    t.feed_id,
    t.trip_id,
    t.rt,
    t.direction,
    t.block_id,
    t.pid,
    t.first_departure_s as start_s,
    t.last_departure_s as end_s,
    {{ service_ts('a.service_date', 't.first_departure_s') }} as scheduled_start,
    {{ service_ts('a.service_date', 't.last_departure_s') }} as scheduled_end
from {{ ref('int_active_services') }} as a
inner join {{ ref('stg_gtfs_bus_trips') }} as t
    on a.feed_id = t.feed_id and a.service_id = t.service_id

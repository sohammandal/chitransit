-- Every 'L' trip of every loaded GTFS feed. rt is the line's Train Tracker code, the GTFS
-- route_id lowercased (Red -> red, Brn -> brn, G -> g), as the live train feed names lines.
select
    t.feed_id,
    t.trip_id,
    t.service_id,
    lower(t.route_id) as rt
from {{ source('gtfs', 'trips') }} as t
inner join {{ source('gtfs', 'routes') }} as r on t.feed_id = r.feed_id and t.route_id = r.route_id
where r.route_type = '1'

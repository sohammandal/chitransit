-- Bus routes per GTFS feed: route id (= Bus Tracker rt) and its public name.
select
    feed_id,
    route_id as rt,
    route_long_name as name
from {{ source('gtfs', 'routes') }}
where route_type = '3'

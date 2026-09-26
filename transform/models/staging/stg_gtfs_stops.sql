-- GTFS stop locations per feed (stop_id = Bus Tracker stpid for 99.8% of pattern stops).
select
    feed_id,
    stop_id,
    stop_lat::double as lat,
    stop_lon::double as lon
from {{ source('gtfs', 'stops') }}

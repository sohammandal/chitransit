-- Scheduled departures at each stop for every trip in int_scheduled_trips (the feed in
-- force that day). is_terminal marks the trip's first and last stop.
-- Grain: (service_date, trip_id, stop_sequence).
with departures as (
select
    s.service_date,
    s.trip_id,
    s.rt,
    s.direction,
    st.stop_id,
    st.stop_sequence::int as stop_sequence,
    {{ service_ts('s.service_date', gtfs_seconds('st.departure_time')) }} as departs_at
from {{ ref('int_scheduled_trips') }} as s
inner join {{ source('gtfs', 'stop_times') }} as st
    on s.feed_id = st.feed_id and s.trip_id = st.trip_id
)

select
    *,
    stop_sequence = min(stop_sequence) over trip
    or stop_sequence = max(stop_sequence) over trip as is_terminal
from departures
window trip as (partition by service_date, trip_id)

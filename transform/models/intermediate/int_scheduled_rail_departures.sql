-- Scheduled departures at each 'L' platform for every trip running that day: stop_id is the
-- platform (Train Tracker's stpId: a direction's side of a station). is_last: the trip's last
-- stop, an arrival only. Grain: (service_date, trip_id, stop_sequence).
select
    a.service_date,
    t.trip_id,
    t.rt,
    st.stop_id,
    st.stop_sequence::int as stop_sequence,
    {{ service_ts('a.service_date', gtfs_seconds('st.departure_time')) }} as departs_at,
    st.stop_sequence::int
    = max(st.stop_sequence::int) over (partition by a.service_date, t.trip_id) as is_last
from {{ ref('int_active_services') }} as a
inner join {{ ref('stg_gtfs_rail_trips') }} as t
    on a.feed_id = t.feed_id and a.service_id = t.service_id
inner join {{ source('gtfs', 'stop_times') }} as st
    on t.feed_id = st.feed_id and t.trip_id = st.trip_id

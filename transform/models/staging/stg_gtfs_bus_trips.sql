-- Every bus trip of every loaded GTFS feed, with its first and last departure. CTA's GTFS
-- never starts a trip at or after 24:00 (trips after midnight are on the next date), and
-- shape_id is a 3-digit prefix + the Bus Tracker pid.
{{ config(materialized='table') }}  -- read GTFS stop_times once per run (models and tests reuse it)
with stops as (
    select
        st.feed_id,
        st.trip_id,
        {{ gtfs_seconds('st.departure_time') }} as dep_s,
        st.stop_sequence::int as seq
    from {{ source('gtfs', 'stop_times') }} as st
),

bounds as (
    select
        feed_id,
        trip_id,
        arg_min(dep_s, seq) as first_departure_s,
        arg_max(dep_s, seq) as last_departure_s
    from stops
    group by all
)

select
    t.feed_id,
    t.trip_id,
    t.service_id,
    t.route_id as rt,
    t.direction,
    t.block_id,
    substr(t.shape_id, 4)::int as pid,
    b.first_departure_s,
    b.last_departure_s
from {{ source('gtfs', 'trips') }} as t
inner join {{ source('gtfs', 'routes') }} as r on t.feed_id = r.feed_id and t.route_id = r.route_id
inner join bounds as b on t.feed_id = b.feed_id and t.trip_id = b.trip_id
where r.route_type = '3'

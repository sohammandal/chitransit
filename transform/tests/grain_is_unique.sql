-- Every model's documented grain holds (one failing row per duplicated key).
select 'stg_bus_positions' as model, count(*) as n from {{ ref('stg_bus_positions') }} group by vid, observed_at having count(*) > 1
union all
select 'int_observed_trips', count(*) from {{ ref('int_observed_trips') }} group by service_date, rt, pid, start_s having count(*) > 1
union all
select 'int_trip_flags', count(*) from {{ ref('int_trip_flags') }} group by service_date, rt, direction, start_s, stop_id having count(*) > 1
union all
select 'int_stop_passes', count(*) from {{ ref('int_stop_passes') }} group by service_date, rt, pid, start_s, vid, seq having count(*) > 1
union all
select 'int_scheduled_departures', count(*) from {{ ref('int_scheduled_departures') }} group by service_date, trip_id, stop_sequence having count(*) > 1
union all
select 'stg_gtfs_bus_trips', count(*) from {{ ref('stg_gtfs_bus_trips') }} group by feed_id, trip_id having count(*) > 1
union all
select 'route_hour_delivery', count(*) from {{ ref('route_hour_delivery') }} group by service_date, rt, direction, start_hour having count(*) > 1
union all
select 'stop_hour_service', count(*) from {{ ref('stop_hour_service') }} group by service_date, rt, direction, stop_id, hour_start having count(*) > 1

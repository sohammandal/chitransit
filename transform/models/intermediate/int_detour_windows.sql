-- Times a route ran buses on patterns its GTFS schedule doesn't have (detours, reroutes),
-- widened by an hour each side. Missing trips on that route then are not called ghosts:
-- their buses may be running the detour at a different start time.
with scheduled_patterns as (
    select distinct service_date, rt, pid from {{ ref('int_scheduled_trips') }}
)

select
    o.service_date,
    o.rt,
    o.pid,
    o.first_seen_at - interval 1 hour as window_start,
    o.last_seen_at + interval 1 hour as window_end
from {{ ref('int_observed_trips') }} as o
left join scheduled_patterns as s
    on o.service_date = s.service_date and o.rt = s.rt and o.pid = s.pid
where s.pid is null

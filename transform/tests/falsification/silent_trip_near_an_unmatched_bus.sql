{{ config(severity='warn') }}
-- Falsification: a silent/missing trip with a bus on the same route and pattern that no
-- scheduled trip claimed, starting within 30 minutes of it. It may be this trip under a
-- trip key more than 2 minutes off schedule (a false ghost). Each row needs a look.
with ghosts as (
    select t.service_date, t.trip_id, t.rt, t.pid, s.start_s
    from {{ ref('trip_status') }} as t
    inner join {{ ref('int_scheduled_trips') }} as s using (service_date, trip_id)
    where t.status in ('silent', 'missing')
),

unclaimed as (
    select o.*
    from {{ ref('int_observed_trips') }} as o
    left join {{ ref('int_trip_matches') }} as m
        on
            o.service_date = m.service_date and o.pid = m.observed_pid
            and o.first_seen_at = m.first_seen_at
    where m.trip_id is null
)

select g.service_date, g.trip_id, g.rt, g.pid, g.start_s, u.start_s as bus_start_s, u.first_vid
from ghosts as g
inner join unclaimed as u
    on
        g.service_date = u.service_date and g.rt = u.rt and g.pid = u.pid
        and abs(g.start_s - u.start_s) <= 1800

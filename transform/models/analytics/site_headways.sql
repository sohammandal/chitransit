-- Scheduled gap between buses per route, direction and hour (Chicago), for the site's "right
-- now" judgments: 60 minutes / trips starting that hour. Built for today and tomorrow by a
-- second, date-bounded run in the nightly job (the window vars pick the dates).
select
    service_date,
    rt,
    direction,
    hour(timezone('America/Chicago', scheduled_start)) as hour,
    count(*) as trips,
    round(60.0 / count(*), 1) as headway_min
from {{ ref('int_scheduled_trips') }}
group by all

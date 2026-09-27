-- One row per route and service date: the numbers a route page shows for a finished day.
-- Silent is a range: proven (strict physical evidence) to possible (all silent).
select
    service_date,
    rt,
    count(*) as scheduled,
    count(*) filter (where status in ('ran', 'likely_ran')) as ran,
    count(*) filter (where status = 'flagged') as announced,
    count(*) filter (where status = 'silent' and strict_evidence) as silent_proven,
    count(*) filter (where status = 'silent') as silent_possible,
    count(*) filter (where status in ('missing', 'unknown')) as not_judged
from {{ ref('trip_status') }}
where status != 'pending'
group by all

-- Delivery per route, direction and scheduled start hour (Chicago), from trip_status.
-- Pending trips (not over yet) are left out.
select
    service_date,
    rt,
    direction,
    hour(scheduled_start) as start_hour,
    count(*) as scheduled,
    count(*) filter (where status = 'ran') as ran,
    count(*) filter (where status = 'likely_ran') as likely_ran,
    count(*) filter (where status = 'flagged') as flagged,
    count(*) filter (where status = 'silent') as silent,
    count(*) filter (where status = 'silent' and strict_evidence) as silent_strict,
    count(*) filter (where status = 'missing') as missing,
    count(*) filter (where status = 'unknown') as unknown,
    count(*) filter (where status = 'ran' and flagged) as flagged_but_ran,
    round(avg(sweep_coverage), 4) as avg_sweep_coverage
from {{ ref('trip_status') }}
where status != 'pending'
group by all

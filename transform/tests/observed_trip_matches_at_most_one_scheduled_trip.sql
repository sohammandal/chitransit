-- An observed trip may explain at most one scheduled trip.
select service_date, rt, observed_pid, n_reports, count(*) as n
from {{ ref('trip_status') }}
where observed_pid is not null
group by service_date, rt, observed_pid, first_seen_at, n_reports
having count(*) > 1

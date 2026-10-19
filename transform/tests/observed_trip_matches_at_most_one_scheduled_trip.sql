-- An observed trip may explain at most one scheduled trip. An observed trip is its pattern and
-- start (int_trip_matches' own key), not when it was first seen: two #49 buses on one pattern,
-- first seen in the same sweep with the same number of reports, read as one bus matched twice
-- and failed every nightly from Oct 18.
select service_date, observed_pid, observed_start_s, count(*) as n
from {{ ref('int_trip_matches') }}
group by service_date, observed_pid, observed_start_s
having count(*) > 1

{{ config(severity='warn') }}
-- Falsification: CTA flagged a trip canceled at a stop, then a later sample at that stop showed
-- it not canceled. trip_status ignores such flags; each one is worth a look (did the trip run,
-- or did the flag just drop off?). None through Sep 26.
select service_date, rt, direction, start_s, stop_id, last_flagged_at, cleared_at
from {{ ref('int_trip_flags') }}
where cleared_at is not null

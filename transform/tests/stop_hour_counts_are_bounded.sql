-- Bunched and gap headways are subsets of the observed passes.
select *
from {{ ref('stop_hour_service') }}
where
    bunched + gaps > observed_buses
    or bunched < 0 or gaps < 0
    or (median_headway_min is not null and median_headway_min < 0)

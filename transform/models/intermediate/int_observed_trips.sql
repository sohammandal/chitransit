-- One row per trip the feed showed: (rt, pid, stsd, stst) with the after-midnight rule
-- and DST rules applied (macros/trip_start.sql). Buses report trips after midnight as stst >=
-- 86,400 on the previous stsd; CTA's GTFS lists them on the next date (never at or after 24:00).
with keyed as (
    select
        *,
        {{ trip_service_date('stsd', 'stst') }} as service_date,
        {{ trip_start_s('stsd', 'stst') }} as start_s
    from {{ ref('stg_bus_positions') }}
    where stsd is not null and stst is not null and pid is not null
)

select
    service_date,
    rt,
    pid,
    start_s,
    min(observed_at) as first_seen_at,
    max(observed_at) as last_seen_at,
    count(*) as n_reports,
    count(distinct vid) as n_buses,
    -- ties broken by vid: two buses can report a trip in the same second (#78, Oct 1), and
    -- an unbroken tie picks whichever thread finished first
    arg_min(vid, (observed_at, vid)) as first_vid,
    arg_max(vid, (observed_at, vid)) as last_vid,
    min(pdist) as min_pdist,
    max(pdist) as max_pdist
from keyed
where service_date between {{ window_start() }} and {{ window_end() }}
group by all

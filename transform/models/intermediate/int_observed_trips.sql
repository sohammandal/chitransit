-- One row per trip the feed showed: (rt, pid, stsd, stst) with the after-midnight rule
-- applied. Buses report trips after midnight as stst >= 86,400 on the previous stsd, while
-- CTA's GTFS lists them on the next date (it never starts a trip at or after 24:00).
with keyed as (
    select
        *,
        case when stst >= 86400 then stsd + 1 else stsd end as service_date,
        case when stst >= 86400 then stst - 86400 else stst end as start_s
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
    arg_min(vid, observed_at) as first_vid,
    arg_max(vid, observed_at) as last_vid,
    min(pdist) as min_pdist,
    max(pdist) as max_pdist
from keyed
where service_date between {{ window_start() }} and {{ window_end() }}
group by all

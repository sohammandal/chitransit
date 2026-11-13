-- Trips CTA flagged canceled (dyn = 1) in sampled predictions, keyed like
-- int_observed_trips, per stop: predictions carry no pid, so trip_status matches a flag to
-- a scheduled trip that serves that stop (two trips can share route, direction and start).
-- cleared_at: a later sample at the same stop showed the trip not canceled (CTA reversed the
-- flag); trip_status then ignores the flag. None seen through Sep 26 (495 flagged pairs).
with keyed as (
    select
        *,
        {{ trip_service_date('stsd', 'stst') }} as service_date,
        {{ trip_start_s('stsd', 'stst') }} as start_s,
        replace(rtdir, 'bound', '') as direction
    from {{ ref('stg_predictions') }}
    where stsd is not null and stst is not null
)

select
    service_date,
    rt,
    direction,
    start_s,
    stpid as stop_id,
    min(fetched_at) filter (where dyn = 1) as first_flagged_at,
    max(fetched_at) filter (where dyn = 1) as last_flagged_at,
    count(*) filter (where dyn = 1) as n_flagged_samples,
    case
        when max(fetched_at) filter (where dyn <> 1) > last_flagged_at
            then max(fetched_at) filter (where dyn <> 1)
    end as cleared_at
from keyed
where service_date between {{ window_start() }} and {{ window_end() }}
group by all
having n_flagged_samples > 0

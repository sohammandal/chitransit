-- Trips CTA flagged canceled (dyn = 1) in sampled predictions, keyed like
-- int_observed_trips, per stop: predictions carry no pid, so trip_status matches a flag to
-- a scheduled trip that serves that stop (two trips can share route, direction and start).
with keyed as (
    select
        *,
        case when stst >= 86400 then stsd + 1 else stsd end as service_date,
        case when stst >= 86400 then stst - 86400 else stst end as start_s,
        replace(rtdir, 'bound', '') as direction
    from {{ ref('stg_predictions') }}
    where dyn = 1 and stsd is not null and stst is not null
)

select
    service_date,
    rt,
    direction,
    start_s,
    stpid as stop_id,
    min(fetched_at) as first_flagged_at,
    max(fetched_at) as last_flagged_at,
    count(*) as n_flagged_samples
from keyed
where service_date between {{ window_start() }} and {{ window_end() }}
group by all

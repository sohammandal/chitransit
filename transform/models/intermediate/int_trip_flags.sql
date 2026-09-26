-- Trips CTA flagged canceled (dyn = 1) in any sampled prediction, keyed like
-- int_observed_trips. Predictions carry the direction word but no pid.
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
    min(fetched_at) as first_flagged_at,
    max(fetched_at) as last_flagged_at,
    count(*) as n_flagged_samples
from keyed
where service_date between {{ window_start() }} and {{ window_end() }}
group by all

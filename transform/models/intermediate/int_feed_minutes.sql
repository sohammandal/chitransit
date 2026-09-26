-- Per minute: did a sweep with every call succeeding run, and was getpredictions sampled?
-- The gate for calling a trip a silent ghost (absence only counts when we were looking).
with sweeps as (
    select date_trunc('minute', started_at) as minute, bool_or(calls_ok = calls_total) as swept
    from {{ ref('stg_sweeps') }}
    group by 1
),

predictions as (
    select distinct date_trunc('minute', fetched_at) as minute
    from {{ ref('stg_predictions') }}
)

select
    coalesce(s.minute, p.minute) as minute,
    coalesce(s.swept, false) as swept,
    p.minute is not null as predictions_sampled
from sweeps as s
full outer join predictions as p on s.minute = p.minute

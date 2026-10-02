-- One row per 30 s sweep (feed health: how many of its calls succeeded).
{{ config(materialized='table') }}  -- read Ingress once per run (models and tests reuse it)
with src as (
    select *
    from {{ source('ingress', 'ghost_ops_sweeps') }}
    where
        started_at >= {{ service_ts(window_start() ~ " - 1", 0) }}
        and started_at < {{ service_ts(window_end() ~ " + 2", 0) }}
)

select sweep_id, started_at, finished_at, calls_total, calls_ok, vehicles
from src
where feed = 'bus'
qualify row_number() over (partition by sweep_id order by __lsn) = 1

-- One row per bus report (vid, observed_at), deduplicated: CDC is at-least-once.
-- Bounded to the build window, padded for trips that start before or end after it.
with src as (
    select *
    from {{ source('ingress', 'ghost_ops_bus_positions') }}
    where
        observed_at >= {{ service_ts(window_start() ~ " - 1", 0) }}
        and observed_at < {{ service_ts(window_end() ~ " + 2", 0) }}
)

select
    vid,
    observed_at,
    fetched_at,
    rt,
    pid,
    pdist,
    lat,
    lon,
    dly,
    stsd,
    stst,
    tablockid,
    psgld
from src
qualify row_number() over (partition by vid, observed_at order by __lsn) = 1

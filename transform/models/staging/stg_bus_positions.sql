-- One row per bus report (vid, observed_at), deduplicated: CDC is at-least-once.
-- Bounded to the build window, padded for trips that start before or end after it.
-- CDC copies of a report are identical (positions are insert-only), so DISTINCT drops them: a
-- hash that spills to disk, where ranking every row in a window held them all (3.4 GB, Oct 2).
-- tests/grain_is_unique fails the build if two different rows ever share (vid, observed_at).
{{ config(materialized='table') }}  -- read Ingress once per run (models and tests reuse it)
select distinct
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
from {{ source('ingress', 'ghost_ops_bus_positions') }}
where
    observed_at >= {{ service_ts(window_start() ~ " - 1", 0) }}
    and observed_at < {{ service_ts(window_end() ~ " + 2", 0) }}

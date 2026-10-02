-- One row per getpredictions call (poll_id), deduplicated: CDC is at-least-once. Read from
-- Ingress once per run for the two models that need it (stg_predictions, stg_prediction_samples).
{{ config(materialized='table') }}
select poll_id, fetched_at, request_params, error
from {{ source('ingress', 'ghost_ops_raw_polls') }}
where
    endpoint = 'getpredictions'
    and fetched_at >= {{ service_ts(window_start() ~ " - 1", 0) }}
    and fetched_at < {{ service_ts(window_end() ~ " + 2", 0) }}
qualify row_number() over (partition by poll_id order by __lsn) = 1

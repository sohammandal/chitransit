-- One row per stop per getpredictions call: which stops were sampled, and when. (A call's
-- response lists only stops with upcoming buses, so this comes from the request.)
with polls as (
    select poll_id, fetched_at, request_params
    from {{ ref('stg_prediction_polls') }}
    where error is null
)

select
    poll_id,
    fetched_at,
    unnest(string_split(json_extract_string(request_params, '$.stpid'), ',')) as stop_id
from polls

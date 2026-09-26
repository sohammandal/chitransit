-- One row per stop per getpredictions call: which stops were sampled, and when. (A call's
-- response lists only stops with upcoming buses, so this comes from the request.)
with polls as (
    select poll_id, fetched_at, request_params
    from {{ source('ingress', 'ghost_ops_raw_polls') }}
    where
        endpoint = 'getpredictions'
        and error is null
        and fetched_at >= {{ service_ts(window_start() ~ " - 1", 0) }}
        and fetched_at < {{ service_ts(window_end() ~ " + 2", 0) }}
    qualify row_number() over (partition by poll_id order by __lsn) = 1
)

select
    poll_id,
    fetched_at,
    unnest(string_split(json_extract_string(request_params, '$.stpid'), ',')) as stop_id
from polls

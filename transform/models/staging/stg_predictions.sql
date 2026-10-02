-- One row per prediction in a sampled getpredictions response (raw_responses: the exact
-- bytes CTA sent). dyn = 1 marks the trip as canceled at that stop, 4 as expressed.
-- A table, built once: two models read it, and it parses JSON out of raw responses.
{{ config(materialized='table') }}
with polls as (
    select poll_id, fetched_at
    from {{ source('ingress', 'ghost_ops_raw_polls') }}
    where
        endpoint = 'getpredictions'
        and fetched_at >= {{ service_ts(window_start() ~ " - 1", 0) }}
        and fetched_at < {{ service_ts(window_end() ~ " + 2", 0) }}
    qualify row_number() over (partition by poll_id order by __lsn) = 1
),

responses as (
    -- Every copy, no window over the bodies: ranking them held all the JSON (several GB over
    -- the read window) in memory and ran the runner out on 2026-10-02. CDC copies of a poll
    -- are byte-identical, so they're dropped after parsing, on the narrow rows (below).
    select poll_id, body
    from {{ source('ingress', 'ghost_ops_raw_responses') }}
    where
        fetched_at >= {{ service_ts(window_start() ~ " - 1", 0) }}
        and fetched_at < {{ service_ts(window_end() ~ " + 2", 0) }}
        -- only prediction responses: the vehicle responses (~286 KB each, 13 a sweep) filled
        -- the runner's memory on 2026-09-28
        and poll_id in (select polls.poll_id from polls)
),

prd as (
    select
        poll_id,
        fetched_at,
        x.i,
        x.p
    from (
        select
            polls.poll_id,
            polls.fetched_at,
            -- each prediction with its place in the response, in one unnest
            unnest(list_transform(
                from_json(json_extract(responses.body, '$."bustime-response".prd'), '["JSON"]'),
                (p, i) -> {'i': i, 'p': p}
            )) as x
        from polls
        inner join responses using (poll_id)
    )
),

-- One row per prediction and its place in the response: CDC copies of a poll collapse, and
-- a response that lists the same prediction twice keeps both (489 rows on Oct 1)
deduplicated as (
    select distinct
        poll_id,
        fetched_at,
        i,
        p ->> 'stpid' as stpid,
        p ->> 'rt' as rt,
        p ->> 'rtdir' as rtdir,
        p ->> 'vid' as vid,
        p ->> 'typ' as typ,
        (p ->> 'dyn')::int as dyn,
        (p ->> 'stsd')::date as stsd,
        (p ->> 'stst')::int as stst
    from prd
)

select poll_id, fetched_at, stpid, rt, rtdir, vid, typ, dyn, stsd, stst
from deduplicated

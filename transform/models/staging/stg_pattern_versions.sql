-- Every version of every pattern (SCD2), from the patterns change log: a pattern's stop list
-- can change under the same pid, so passes are interpolated against the version in force
-- when the bus reported, like schedules against the feed in force that day.
-- valid_from = the version's changed_at (the first version is open back to -infinity, since a
-- bus is seen a moment before its pattern is first fetched); valid_to = the next version's.
{{ config(materialized='table') }}  -- read Ingress once per run (models and tests reuse it)
with changes as (
    select pid, rt, rtdir, content_hash, changed_at, pattern, __lsn
    from {{ source('ingress', 'ghost_ops_patterns') }}
    where __op != 'd'
),

versions as (
    -- one row per content version (CDC is at-least-once; unchanged rows repeat)
    select *
    from changes
    qualify row_number() over (partition by pid, content_hash order by __lsn) = 1
)

select
    pid,
    rt,
    replace(rtdir, 'bound', '') as direction,
    content_hash,
    case
        when row_number() over (partition by pid order by changed_at, __lsn) = 1
            then '-infinity'::timestamptz
        else changed_at
    end as valid_from,
    lead(changed_at) over (partition by pid order by changed_at, __lsn) as valid_to,
    pattern
from versions

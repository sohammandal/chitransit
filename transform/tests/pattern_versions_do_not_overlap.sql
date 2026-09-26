-- SCD2: a pid's versions tile time with no overlaps or holes: each version ends exactly where
-- the next begins, and none is empty.
with versions as (
    select
        pid,
        valid_from,
        valid_to,
        lead(valid_from) over (partition by pid order by valid_from) as next_from
    from {{ ref('stg_pattern_versions') }}
)

select pid, valid_from, valid_to, next_from
from versions
where
    valid_to <= valid_from
    or valid_to is distinct from next_from

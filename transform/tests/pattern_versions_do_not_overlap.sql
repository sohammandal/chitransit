-- SCD2: a pid's versions tile time without overlaps or zero-length versions.
select pid, valid_from, valid_to
from {{ ref('stg_pattern_versions') }}
where
    valid_to <= valid_from
    or valid_to != (
        select min(v2.valid_from) from {{ ref('stg_pattern_versions') }} as v2
        where v2.pid = stg_pattern_versions.pid and v2.valid_from > stg_pattern_versions.valid_from
    )

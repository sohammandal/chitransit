-- Stops of every pattern version, with their distance along it (feet). Join on pid and a time
-- inside [valid_from, valid_to) to get the stop list in force.
with points as (
    select
        pid,
        valid_from,
        valid_to,
        unnest(from_json(json_extract(pattern, '$.pt'), '["JSON"]')) as pt
    from {{ ref('stg_pattern_versions') }}
)

select
    pid,
    valid_from,
    valid_to,
    (pt ->> 'seq')::int as seq,
    pt ->> 'stpid' as stop_id,
    pt ->> 'stpnm' as stop_name,
    (pt ->> 'pdist')::double as pdist
from points
where pt ->> 'typ' = 'S'

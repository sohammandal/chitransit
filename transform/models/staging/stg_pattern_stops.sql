-- Stops of the latest version of each pattern, with their distance along it (feet). CTA
-- gives a reroute a new pid, so a pid's stops rarely change; the latest version is used.
with patterns as (
    select pid, pattern
    from {{ source('ingress', 'ghost_ops_patterns') }}
    where __op != 'd'
    qualify row_number() over (partition by pid order by __lsn desc) = 1
),

points as (
    select
        pid,
        unnest(from_json(json_extract(pattern, '$.pt'), '["JSON"]')) as pt
    from patterns
)

select
    pid,
    (pt ->> 'seq')::int as seq,
    pt ->> 'stpid' as stop_id,
    pt ->> 'stpnm' as stop_name,
    (pt ->> 'pdist')::double as pdist
from points
where pt ->> 'typ' = 'S'

-- Every pattern's shape and stops as of the run, for the site's live map (one JSON per pid,
-- transform/site_data.py). The version in force at as_of: detours and reroutes change a
-- pattern's points under the same pid.
with current_version as (
    select pid, rt, direction, pattern
    from {{ ref('stg_pattern_versions') }}
    where valid_from <= {{ as_of() }} and (valid_to is null or valid_to > {{ as_of() }})
),

points as (
    select
        pid,
        rt,
        direction,
        unnest(from_json(json_extract(pattern, '$.pt'), '["JSON"]')) as pt
    from current_version
)

select
    pid,
    any_value(rt) as rt,
    any_value(direction) as direction,
    list([round((pt ->> 'lon')::double, 5), round((pt ->> 'lat')::double, 5)]
        order by (pt ->> 'seq')::int) as coords,
    list(
        {
            'lon': round((pt ->> 'lon')::double, 5),
            'lat': round((pt ->> 'lat')::double, 5),
            'name': pt ->> 'stpnm',
            'stop_id': pt ->> 'stpid',
            'pdist': round((pt ->> 'pdist')::double)
        }
        order by (pt ->> 'seq')::int
    ) filter (where pt ->> 'typ' = 'S') as stops
from points
group by pid

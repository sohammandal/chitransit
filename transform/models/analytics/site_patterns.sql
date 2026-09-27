-- Every pattern's shape and stops as of the run, for the site's live map (one JSON per pid,
-- transform/site_data.py). The version in force at as_of: detours and reroutes change a
-- pattern's points under the same pid. Each stop carries how many routes serve its
-- intersection ("Clark & Belmont" on #22 and "Belmont & Clark" on #77 are one crossing), so
-- the site can label a route's major cross streets.
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
),

intersections as (
    -- "Clark & Belmont" and "Belmont & Clark" -> "belmont|clark"
    select
        pid,
        rt,
        pt ->> 'stpid' as stop_id,
        array_to_string(
            list_sort(list_transform(string_split(lower(pt ->> 'stpnm'), '&'), x -> trim(x))), '|'
        ) as crossing
    from points
    where pt ->> 'typ' = 'S'
),

crossing_routes as (
    select crossing, count(distinct rt) as routes
    from intersections
    group by crossing
),

stop_routes as (
    select distinct i.stop_id, c.routes
    from intersections as i
    inner join crossing_routes as c on i.crossing = c.crossing
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
            'pdist': round((pt ->> 'pdist')::double),
            'routes': coalesce(sr.routes, 1)
        }
        order by (pt ->> 'seq')::int
    ) filter (where pt ->> 'typ' = 'S') as stops
from points
left join stop_routes as sr on points.pt ->> 'stpid' = sr.stop_id
group by pid

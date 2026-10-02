-- Which observed trip (if any) explains each scheduled trip: same pid and start (GTFS starts
-- 0-120 s after the bus's stst), or, among the leftovers on both sides, same route and start
-- (a bus on a detour pattern GTFS doesn't have). One observed trip explains at most one
-- scheduled trip. Grain: (service_date, trip_id), matched trips only.
with scheduled as (
    select * from {{ ref('int_scheduled_trips') }}
),

observed as (
    select o.*, p.direction as observed_direction
    from {{ ref('int_observed_trips') }} as o
    left join {{ ref('stg_patterns') }} as p on o.pid = p.pid
),

by_pattern as (
    select
        s.service_date,
        s.trip_id,
        o.pid as observed_pid,
        o.start_s as observed_start_s,
        o.n_reports,
        o.first_seen_at,
        o.last_seen_at,
        o.first_vid,
        o.last_vid
    from scheduled as s
    inner join observed as o
        on
            s.service_date = o.service_date and s.rt = o.rt and s.pid = o.pid
            and s.start_s - o.start_s between 0 and {{ var('rule_start_tolerance_s') }}
    qualify
        row_number() over (partition by s.service_date, s.trip_id order by s.start_s - o.start_s) = 1
        and row_number() over (
            partition by o.service_date, o.rt, o.pid, o.start_s order by s.start_s - o.start_s, s.trip_id
        ) = 1  -- trip_id breaks a tie between scheduled trips (same pattern, same start)
),

by_route as (
    -- Leftovers on both sides: buses on patterns GTFS doesn't have (detours).
    select
        s.service_date,
        s.trip_id,
        o.pid as observed_pid,
        o.start_s as observed_start_s,
        o.n_reports,
        o.first_seen_at,
        o.last_seen_at,
        o.first_vid,
        o.last_vid
    from scheduled as s
    inner join observed as o
        on
            s.service_date = o.service_date and s.rt = o.rt
            and s.start_s - o.start_s between 0 and {{ var('rule_start_tolerance_s') }}
    where
        not exists (
            select 1 from by_pattern as m
            where m.service_date = s.service_date and m.trip_id = s.trip_id
        )
        and not exists (
            select 1 from by_pattern as m
            where
                m.service_date = o.service_date and m.observed_pid = o.pid
                and m.observed_start_s = o.start_s
        )
    qualify
        row_number() over (
            partition by s.service_date, s.trip_id
            order by (o.observed_direction = s.direction) desc, s.start_s - o.start_s
        ) = 1
        and row_number() over (
            partition by o.service_date, o.rt, o.pid, o.start_s
            order by (o.observed_direction = s.direction) desc, s.start_s - o.start_s
        ) = 1
),

matched as (
    select *, 'pattern' as matched_on from by_pattern
    union all
    select *, 'route' as matched_on from by_route
)

select * from matched

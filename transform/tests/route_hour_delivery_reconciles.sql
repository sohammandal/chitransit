-- Every finished trip is counted exactly once in route_hour_delivery.
with totals as (
    select
        (select count(*) from {{ ref('trip_status') }} where status != 'pending') as trips,
        (select sum(scheduled) from {{ ref('route_hour_delivery') }}) as counted,
        (
            select sum(ran + likely_ran + flagged + silent + missing + unknown)
            from {{ ref('route_hour_delivery') }}
        ) as by_status
)

select * from totals where trips != counted or counted != by_status

-- Every bus trip scheduled on each service date in the window, judged by the GTFS feed in
-- force that day (the newest feed published on or before it; SCD2 on publish date).
with days as (
    select unnest(generate_series({{ window_start() }}, {{ window_end() }}, interval 1 day))::date
        as service_date
),

feeds as (
    select
        feed_id,
        (published_at at time zone 'America/Chicago')::date as valid_from,
        lead((published_at at time zone 'America/Chicago')::date)
            over (order by published_at) as valid_to
    from {{ source('gtfs', 'feeds') }}
),

day_feed as (
    -- Before the first loaded feed, fall back to the oldest one.
    select
        d.service_date,
        coalesce(
            max(f.feed_id) filter (
                where d.service_date >= f.valid_from and (f.valid_to is null or d.service_date < f.valid_to)
            ),
            min(f.feed_id)
        ) as feed_id
    from days as d cross join feeds as f
    group by d.service_date
),

weekly as (
    select
        df.service_date,
        c.feed_id,
        c.service_id
    from day_feed as df
    inner join {{ source('gtfs', 'calendar') }} as c on df.feed_id = c.feed_id
    where
        df.service_date between strptime(c.start_date, '%Y%m%d')::date and strptime(c.end_date, '%Y%m%d')::date
        and case isodow(df.service_date)
            when 1 then c.monday when 2 then c.tuesday when 3 then c.wednesday
            when 4 then c.thursday when 5 then c.friday when 6 then c.saturday
            else c.sunday
        end = '1'
),

exceptions as (
    select
        df.service_date,
        cd.feed_id,
        cd.service_id,
        cd.exception_type
    from day_feed as df
    inner join {{ source('gtfs', 'calendar_dates') }} as cd
        on df.feed_id = cd.feed_id and df.service_date = strptime(cd.date, '%Y%m%d')::date
),

active as (
    select service_date, feed_id, service_id from weekly
    union
    select service_date, feed_id, service_id from exceptions where exception_type = '1'
    except
    select service_date, feed_id, service_id from exceptions where exception_type = '2'
)

select
    a.service_date,
    t.feed_id,
    t.trip_id,
    t.rt,
    t.direction,
    t.block_id,
    t.pid,
    t.first_departure_s as start_s,
    t.last_departure_s as end_s,
    {{ service_ts('a.service_date', 't.first_departure_s') }} as scheduled_start,
    {{ service_ts('a.service_date', 't.last_departure_s') }} as scheduled_end
from active as a
inner join {{ ref('stg_gtfs_bus_trips') }} as t
    on a.feed_id = t.feed_id and a.service_id = t.service_id

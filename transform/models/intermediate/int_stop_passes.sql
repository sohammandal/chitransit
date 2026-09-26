-- When each bus passed each stop, interpolated between consecutive reports on the same trip:
-- a stop whose distance along the pattern lies between two reports is passed in proportion
-- to the distance covered. Only forward progress counts: a report behind the furthest point
-- the bus already reached on this trip (pdist can jump back, e.g. to 0 at the terminal) is
-- skipped, and report pairs more than 5 minutes apart (the bus dropped out of the feed) are
-- not interpolated. Stops come from the pattern version in force at the report (SCD2).
-- Grain: (service_date, rt, pid, start_s, vid, seq).
with positions as (
    select
        vid,
        observed_at,
        rt,
        pid,
        pdist,
        case when stst >= 86400 then stsd + 1 else stsd end as service_date,
        case when stst >= 86400 then stst - 86400 else stst end as start_s
    from {{ ref('stg_bus_positions') }}
    where pid is not null and stst is not null and stsd is not null and pdist is not null
),

steps as (
    select
        *,
        lead(observed_at) over trip as next_at,
        lead(pdist) over trip as next_pdist,
        max(pdist) over (trip rows between unbounded preceding and current row) as furthest
    from positions
    window trip as (partition by vid, rt, pid, service_date, start_s order by observed_at)
),

moving as (
    select *
    from steps
    where
        next_at is not null
        and pdist = furthest
        and next_pdist > pdist
        and next_at - observed_at <= interval 5 minute
        and service_date between {{ window_start() }} and {{ window_end() }}
)

select
    m.service_date,
    m.rt,
    m.pid,
    m.start_s,
    m.vid,
    s.seq,
    s.stop_id,
    to_timestamp(
        epoch(m.observed_at)
        + (s.pdist - m.pdist) / (m.next_pdist - m.pdist) * (epoch(m.next_at) - epoch(m.observed_at))
    ) as passed_at
from moving as m
inner join {{ ref('stg_pattern_stops') }} as s
    on
        m.pid = s.pid
        and m.observed_at >= s.valid_from
        and (s.valid_to is null or m.observed_at < s.valid_to)
        -- inclusive at both ends so terminals count; a stop exactly on a report shared by two
        -- steps keeps its earliest pass (qualify below)
        and s.pdist >= m.pdist
        and s.pdist <= m.next_pdist
qualify row_number() over (
    partition by m.service_date, m.rt, m.pid, m.start_s, m.vid, s.seq order by passed_at
) = 1

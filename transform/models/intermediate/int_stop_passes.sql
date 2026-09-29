-- When each bus passed each stop, with the live feed's rules (ghost_ledger.live.trip_passes:
-- one definition for live waits and history). Per trip (vid, rt, pid, service date, start):
--   * The trip gets under way at its first report rule_start_ft past the lowest point it has
--     reported so far: buses lay over mid-pattern (#146) and creep a few feet meanwhile.
--   * Stops crossed before that keep their last crossing, and never earlier than the bus's
--     last report before it left: it was still there (first-stop passes vs the schedule,
--     Sep 28: median +5 s; the old earliest-crossing rule put 21.5% over 3 minutes early).
--   * After that, a step forward passes the stops between the furthest point reached since
--     departure and its end, inclusive and interpolated along the step; each stop keeps its
--     earliest pass. A jump back (to 0, GPS noise) passes nothing twice and loses nothing
--     beyond the furthest point once the bus is past it again.
--   * Across a blackout (reports more than 5 minutes apart) the stops are passed at the later
--     report, the latest the bus can have passed them (J14 goes dark on Lake Shore Drive), and
--     marked bounded: the bus served the stop, but when is only known to be no later (0.7% of
--     passes, Sep 28). Headways next to one aren't timed (stop_hour_service).
-- Stops come from the pattern version in force at the report (SCD2).
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

lowest as (
    select
        *,
        min(pdist) over (
            partition by vid, rt, pid, service_date, start_s
            order by observed_at rows between unbounded preceding and current row
        ) as lowest_so_far
    from positions
),

departures as (
    select
        vid,
        rt,
        pid,
        service_date,
        start_s,
        min(observed_at) filter (
            where pdist >= lowest_so_far + {{ var('rule_start_ft') }}
        ) as left_at
    from lowest
    group by all
),

marked as (
    select
        p.*,
        d.left_at,
        max(p.observed_at) filter (where p.observed_at < d.left_at) over (
            partition by p.vid, p.rt, p.pid, p.service_date, p.start_s
        ) as waited
    from positions as p
    inner join departures as d
        using (vid, rt, pid, service_date, start_s)
    where d.left_at is not null
),

steps as (
    select
        *,
        lead(observed_at) over trip as next_at,
        lead(pdist) over trip as next_pdist,
        max(case when observed_at >= left_at then pdist end) over (
            trip rows between unbounded preceding and current row
        ) as furthest
    from marked
    window trip as (partition by vid, rt, pid, service_date, start_s order by observed_at)
),

moving as (
    select
        *,
        next_at <= left_at as starting,
        next_at - observed_at > interval 5 minute as blackout,
        case when next_at <= left_at then pdist else furthest end as lo
    from steps
    where
        next_at is not null
        and next_pdist > pdist
        and next_pdist > case when next_at <= left_at then pdist else furthest end
        and service_date between {{ window_start() }} and {{ window_end() }}
),

crossings as (
    select
        m.service_date,
        m.rt,
        m.pid,
        m.start_s,
        m.vid,
        m.starting,
        m.waited,
        m.blackout,
        s.seq,
        s.stop_id,
        case
            when m.blackout then m.next_at
            else to_timestamp(
                epoch(m.observed_at)
                + (s.pdist - m.pdist) / (m.next_pdist - m.pdist)
                * (epoch(m.next_at) - epoch(m.observed_at))
            )
        end as crossed_at
    from moving as m
    inner join {{ ref('stg_pattern_stops') }} as s
        on
            m.pid = s.pid
            and m.observed_at >= s.valid_from
            and (s.valid_to is null or m.observed_at < s.valid_to)
            and s.pdist >= m.lo
            and s.pdist <= m.next_pdist
),

per_stop as (
    select
        service_date,
        rt,
        pid,
        start_s,
        vid,
        seq,
        stop_id,
        greatest(
            max(crossed_at) filter (where starting), max(waited) filter (where starting)
        ) as start_pass,
        arg_max(blackout, crossed_at) filter (where starting) as start_bounded,
        min(crossed_at) filter (where not starting) as run_pass,
        arg_min(blackout, crossed_at) filter (where not starting) as run_bounded
    from crossings
    group by all
)

select
    service_date,
    rt,
    pid,
    start_s,
    vid,
    seq,
    stop_id,
    coalesce(start_pass, run_pass) as passed_at,
    coalesce(case when start_pass is not null then start_bounded end, run_bounded) as bounded
from per_stop

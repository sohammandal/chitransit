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
--   * A trip first seen within rule_first_seen_ft past a stop passed it no later than its first
--     report (bounded): it drove past or pulled out there (#3 at King & 83rd, Sep 28).
-- Stops come from the pattern version in force at the report (SCD2).
-- Grain: (service_date, rt, pid, start_s, vid, seq).
with reports as (
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

-- Runs: a bus's continuous travel along one pattern (live.runs). A new run starts when the
-- pattern changes, or when the bus is back more than rule_terminal_ft on a new trip (it
-- started over). A trip change alone doesn't (CTA reassigns moving buses: #66 8125, Sep 28),
-- nor does a jump back alone (GPS glitches). A run keeps its first report's trip key.
run_starts as (
    select
        *,
        case
            when lag(pid) over bus is null or pid != lag(pid) over bus then 1
            when
                pdist < lag(pdist) over bus - {{ var('rule_terminal_ft') }}
                and (service_date, start_s)
                is distinct from (lag(service_date) over bus, lag(start_s) over bus)
                then 1
            else 0
        end as new_run
    from reports
    window bus as (partition by vid order by observed_at)
),

positions as (
    select
        vid,
        observed_at,
        rt,
        pid,
        pdist,
        run_id,
        first(service_date) over run as service_date,
        first(start_s) over run as start_s
    from (
        select
            *,
            sum(new_run) over (partition by vid order by observed_at rows unbounded preceding)
                as run_id
        from run_starts
    )
    window run as (partition by vid, run_id order by observed_at)
),

lowest as (
    select
        *,
        min(pdist) over (
            partition by vid, run_id, rt, pid, service_date, start_s
            order by observed_at rows between unbounded preceding and current row
        ) as lowest_so_far
    from positions
),

departures as (
    select
        vid,
        run_id,
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
            partition by p.vid, p.run_id, p.rt, p.pid, p.service_date, p.start_s
        ) as waited
    from positions as p
    inner join departures as d
        using (vid, run_id, rt, pid, service_date, start_s)
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
    window trip as (partition by vid, run_id, rt, pid, service_date, start_s order by observed_at)
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
        m.run_id,
        m.starting,
        m.waited,
        m.blackout,
        s.seq,
        s.stop_id,
        s.pdist as stop_ft,
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

first_reports as (
    select
        vid,
        run_id,
        rt,
        pid,
        service_date,
        start_s,
        min(observed_at) as first_at,
        arg_min(pdist, observed_at) as first_pdist
    from marked
    where service_date between {{ window_start() }} and {{ window_end() }}
    group by all
),

first_seen as (
    select
        f.service_date,
        f.rt,
        f.pid,
        f.start_s,
        f.vid,
        f.run_id,
        s.seq,
        s.stop_id,
        s.pdist as stop_ft,
        f.first_at as passed_at
    from first_reports as f
    inner join {{ ref('stg_pattern_stops') }} as s
        on
            f.pid = s.pid
            and f.first_at >= s.valid_from
            and (s.valid_to is null or f.first_at < s.valid_to)
            and s.pdist < f.first_pdist
            and s.pdist >= f.first_pdist - {{ var('rule_first_seen_ft') }}
    qualify row_number() over (
        partition by f.vid, f.run_id, f.rt, f.pid, f.service_date, f.start_s order by s.pdist desc
    ) = 1
),

per_stop as (
    select
        service_date,
        rt,
        pid,
        start_s,
        vid,
        run_id,
        seq,
        stop_id,
        stop_ft,
        greatest(
            max(crossed_at) filter (where starting), max(waited) filter (where starting)
        ) as start_pass,
        arg_max(blackout, crossed_at) filter (where starting) as start_bounded,
        min(crossed_at) filter (where not starting) as run_pass,
        arg_min(blackout, crossed_at) filter (where not starting) as run_bounded
    from crossings
    group by all
),

base_passes as (
    select
        service_date,
        rt,
        pid,
        start_s,
        vid,
        run_id,
        seq,
        stop_id,
        stop_ft,
        coalesce(start_pass, run_pass) as passed_at,
        coalesce(case when start_pass is not null then start_bounded end, run_bounded) as bounded
    from per_stop

    union all

    select
        f.service_date,
        f.rt,
        f.pid,
        f.start_s,
        f.vid,
        f.run_id,
        f.seq,
        f.stop_id,
        f.stop_ft,
        f.passed_at,
        true as bounded
    from first_seen as f
    anti join per_stop as p
        using (service_date, rt, pid, start_s, vid, run_id, seq)
),

-- A bus standing at a stop (within rule_dwell_ft either side, two reports or more in a row
-- after the pass) serves it until it leaves: the pass moves to its last report there (#79
-- stood at Spaulding 11 minutes, Sep 28). Runs of consecutive reports near a stop are islands.
trip_positions as (
    select
        vid,
        run_id,
        rt,
        pid,
        service_date,
        start_s,
        observed_at,
        pdist,
        left_at,
        row_number() over (
            partition by vid, run_id, rt, pid, service_date, start_s order by observed_at
        ) as rn
    from marked
    where service_date between {{ window_start() }} and {{ window_end() }}
),

near_stop as (
    select
        p.vid,
        p.run_id,
        p.rt,
        p.pid,
        p.service_date,
        p.start_s,
        p.rn,
        p.observed_at,
        s.seq,
        p.rn - row_number() over (
            partition by p.vid, p.run_id, p.rt, p.pid, p.service_date, p.start_s, s.seq order by p.rn
        ) as island
    from trip_positions as p
    inner join {{ ref('stg_pattern_stops') }} as s
        on
            p.pid = s.pid
            and p.observed_at >= s.valid_from
            and (s.valid_to is null or p.observed_at < s.valid_to)
            and abs(p.pdist - s.pdist) <= {{ var('rule_dwell_ft') }}
),

islands as (
    select
        vid,
        run_id,
        rt,
        pid,
        service_date,
        start_s,
        seq,
        island,
        min(rn) as first_rn,
        max(rn) as last_rn,
        max(observed_at) as end_at
    from near_stop
    group by all
),

after_pass as (
    select
        b.*,
        p.rn as next_rn
    from base_passes as b
    asof left join trip_positions as p
        on
            b.vid = p.vid and b.run_id = p.run_id and b.rt = p.rt and b.pid = p.pid
            and b.service_date = p.service_date and b.start_s = p.start_s
            and p.observed_at > b.passed_at
),

dwelled as (
    select
        a.service_date,
        a.rt,
        a.pid,
        a.start_s,
        a.vid,
        a.run_id,
        a.seq,
        a.stop_id,
        coalesce(greatest(a.passed_at, i.end_at), a.passed_at) as passed_at,
        a.bounded
    from after_pass as a
    left join islands as i
        on
            a.vid = i.vid and a.run_id = i.run_id and a.rt = i.rt and a.pid = i.pid
            and a.service_date = i.service_date and a.start_s = i.start_s and a.seq = i.seq
            and a.next_rn between i.first_rn and i.last_rn
            and i.last_rn > a.next_rn  -- standing: two reports or more there in a row
),

all_passes as (
    select * from dwelled
)

-- Stops closer together than rule_dwell_ft: a later stop is never passed before an earlier one
select
    service_date,
    rt,
    pid,
    start_s,
    vid,
    seq,
    stop_id,
    min(passed_at) over (
        partition by vid, run_id, rt, pid, service_date, start_s
        order by seq rows between current row and unbounded following
    ) as passed_at,
    bounded
from all_passes

-- Every scheduled departure at every stop, for the poller's live judgments (sched_s,
-- sched_due in ghost_ledger.live and live_trains): all trips of a route count, short turns too,
-- as observed passes do; 'L' platforms too, keyed by the line's Train Tracker code. A trip's
-- last stop is only an arrival and is left out. Built for today and
-- tomorrow by the nightly's second, date-bounded run; transform/live_schedule.py writes it to
-- analytics/ops/stop_departures.json, which only the box reads.
-- Grain: (service_date, trip_id, stop_id, departs_at).
with departures as (
    select
        service_date,
        trip_id,
        rt,
        stop_id,
        departs_at,
        stop_sequence = max(stop_sequence) over (partition by service_date, trip_id) as is_last
    from {{ ref('int_scheduled_departures') }}
)

select distinct service_date, trip_id, rt, stop_id, departs_at
from departures
where not is_last
union all
select distinct service_date, trip_id, rt, stop_id, departs_at
from {{ ref('int_scheduled_rail_departures') }}
where not is_last

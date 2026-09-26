{{ config(severity='warn') }}
-- Falsification: CTA predicted a silent/missing trip, at a stop that trip serves, with a bus
-- assigned and not canceled (dyn = 0) close to when it was due. CTA's system expected it to
-- run; check whether that bus ran it under another key or was reassigned.
with ghosts as (
    select t.service_date, t.trip_id, t.rt, s.start_s
    from {{ ref('trip_status') }} as t
    inner join {{ ref('int_scheduled_trips') }} as s using (service_date, trip_id)
    where t.status in ('silent', 'missing')
),

predicted as (
    select
        p.*,
        case when p.stst >= 86400 then p.stsd + 1 else p.stsd end as service_date,
        case when p.stst >= 86400 then p.stst - 86400 else p.stst end as start_s
    from {{ ref('stg_predictions') }} as p
    where p.dyn = 0 and p.vid is not null
)

select distinct g.service_date, g.trip_id, g.rt, p.vid, p.stpid
from ghosts as g
inner join predicted as p
    on g.service_date = p.service_date and g.rt = p.rt and g.start_s - p.start_s between 0 and 120
inner join {{ ref('int_scheduled_departures') }} as d
    on
        g.service_date = d.service_date and g.trip_id = d.trip_id and p.stpid = d.stop_id
        and p.fetched_at between d.departs_at - interval 20 minute and d.departs_at + interval 5 minute

-- Interpolation only uses forward progress, so a later stop is never passed earlier on a run.
with ordered as (
    select
        *,
        lag(passed_at) over (partition by service_date, rt, pid, start_s, vid, run_id order by seq) as prev_passed_at
    from {{ ref('int_stop_passes') }}
)

select * from ordered where passed_at < prev_passed_at

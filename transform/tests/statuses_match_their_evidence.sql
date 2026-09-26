-- Each status agrees with the evidence behind it.
select service_date, trip_id, status
from {{ ref('trip_status') }}
where
    (status = 'flagged' and not flagged)
    or (status in ('silent', 'missing') and (flagged or n_reports is not null or sweep_coverage < 0.9))
    or (status = 'silent' and not flags_sampled)
    or (status = 'missing' and flags_sampled)
    or (status = 'unknown' and reason not in ('not_watched', 'detour'))
    or (status = 'likely_ran' and reason not in ('tracker_gap', 'single_report'))

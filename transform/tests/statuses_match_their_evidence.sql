-- Each status agrees with the evidence behind it.
select service_date, trip_id, status, reason, ghost_evidence
from {{ ref('trip_status') }}
where
    (status = 'flagged' and not flagged)
    or (status in ('silent', 'missing') and (flagged or n_reports is not null or sweep_coverage < 0.9))
    or (status = 'silent' and not flags_sampled)
    or (status = 'missing' and flags_sampled)
    or (status in ('silent', 'missing') and ghost_evidence in ('bus_on_this_pattern', 'reappeared_far_end', 'dark_between_same_bus'))
    or (strict_evidence and status not in ('silent', 'missing'))
    or (status = 'unknown' and reason not in ('not_watched', 'detour'))
    or (status = 'likely_ran' and reason not in ('tracker_gap', 'single_report', 'miskeyed'))

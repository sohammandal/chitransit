-- status = flagged only for trips CTA flagged, and a ran trip keeps its flag visible.
select service_date, trip_id
from {{ ref('trip_status') }}
where (status = 'flagged' and not flagged) or (status = 'silent' and (flagged or n_reports is not null))

select service_date, trip_id
from {{ ref('trip_status') }}
where status = 'ran' and (n_reports is null or n_reports < 2)

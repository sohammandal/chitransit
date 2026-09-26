select service_date, trip_id, count(*) as n
from {{ ref('trip_status') }}
group by all
having count(*) > 1

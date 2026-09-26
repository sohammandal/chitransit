select service_date, trip_id, count(*) as n
from {{ ref('int_scheduled_trips') }}
group by all
having count(*) > 1

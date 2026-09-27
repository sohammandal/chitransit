-- Route names for the site, from the newest GTFS feed.
with newest as (
    select feed_id from {{ source('gtfs', 'feeds') }}
    order by published_at desc
    limit 1
)

select r.rt, r.name
from {{ ref('stg_gtfs_routes') }} as r
inner join newest using (feed_id)

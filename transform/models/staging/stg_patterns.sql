-- Current direction of each pid (a pid's direction never changes; its stop list can, see
-- stg_pattern_versions).
select pid, rt, direction
from {{ ref('stg_pattern_versions') }}
qualify row_number() over (partition by pid order by valid_from desc) = 1

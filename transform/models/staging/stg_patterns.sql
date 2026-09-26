-- Latest version of each pattern (pid -> route, direction), from its change log.
select
    pid,
    rt,
    replace(rtdir, 'bound', '') as direction
from {{ source('ingress', 'ghost_ops_patterns') }}
where __op != 'd'
qualify row_number() over (partition by pid order by __lsn desc) = 1

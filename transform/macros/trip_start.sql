{# CTA's stst counts real seconds from the service date's local midnight; GTFS counts from
   noon minus 12 hours, the clock time. They differ on the two DST days: on Nov 1, 2026 every
   stst after the 2 AM fall-back read an hour late (measured: first reports 3,600 s before
   stst), no trip matched, and the day showed 67% ran and 2,715 ghosts. #}
{% macro dst_shift_s(stsd) -%}
    (epoch((({{ stsd }})::TIMESTAMP + INTERVAL 12 HOUR)::TIMESTAMPTZ) - 43200
        - epoch((({{ stsd }})::TIMESTAMP)::TIMESTAMPTZ))
{%- endmacro %}

{# A trip's (service_date, start_s) in GTFS terms from CTA's (stsd, stst): the DST shift
   first, then the after-midnight rule (buses report trips after midnight as stst >= 86,400
   on the previous stsd; CTA's GTFS lists them on the next date). #}
{% macro trip_service_date(stsd, stst) -%}
    (case when {{ stst }} - {{ dst_shift_s(stsd) }} >= 86400 then {{ stsd }} + 1 else {{ stsd }} end)
{%- endmacro %}

{% macro trip_start_s(stsd, stst) -%}
    (({{ stst }} - {{ dst_shift_s(stsd) }}) % 86400)::INT
{%- endmacro %}

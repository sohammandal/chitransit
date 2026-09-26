{# The service dates this run builds: vars start_date/end_date, else the last `days`
   complete service days in Chicago (a service day's trips end by ~02:00 the next day). #}
{% macro window_start() -%}
    {%- if var('start_date') -%} DATE '{{ var("start_date") }}'
    {%- else -%} (CURRENT_DATE - INTERVAL '{{ var("days") }} days')::DATE {%- endif -%}
{%- endmacro %}

{% macro window_end() -%}
    {%- if var('end_date') -%} DATE '{{ var("end_date") }}'
    {%- else -%} (CURRENT_DATE - INTERVAL '1 day')::DATE {%- endif -%}
{%- endmacro %}

{# GTFS clock time 'HH:MM:SS' (may pass 24:00) -> seconds after the service day's midnight #}
{% macro gtfs_seconds(col) -%}
    (SPLIT_PART({{ col }}, ':', 1)::INT * 3600 + SPLIT_PART({{ col }}, ':', 2)::INT * 60
        + SPLIT_PART({{ col }}, ':', 3)::INT)
{%- endmacro %}

{# service date + seconds after its midnight -> TIMESTAMPTZ (Chicago) #}
{% macro service_ts(date_col, seconds_col) -%}
    (({{ date_col }})::TIMESTAMP + TO_SECONDS({{ seconds_col }}))::TIMESTAMPTZ
{%- endmacro %}

{# The time a run judges "over yet" against: var as_of (tests, replays), else now. #}
{% macro as_of() -%}
    {%- if var('as_of', none) -%} TIMESTAMPTZ '{{ var("as_of") }}' {%- else -%} now() {%- endif -%}
{%- endmacro %}

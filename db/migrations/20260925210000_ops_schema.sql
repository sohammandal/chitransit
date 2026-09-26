-- migrate:up
-- System of record for the pollers (DDIA ch. 12-13: one writer, everything
-- else derived from this database's change log via CDC).
-- Each 30 s sweep writes sweeps + raw_polls + bus_positions in ONE transaction.
-- Postgres keeps about two days; the full log lives in Ingress (Iceberg on S3).

CREATE SCHEMA IF NOT EXISTS ops;

-- One row per sweep (all routes, every 30 s). Coverage is computed from here.
CREATE TABLE ops.sweeps (
    sweep_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed TEXT NOT NULL CHECK (feed IN ('bus', 'train')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    calls_total INTEGER NOT NULL,
    calls_ok INTEGER NOT NULL,
    vehicles INTEGER NOT NULL
);

CREATE INDEX sweeps_started_at_idx ON ops.sweeps (started_at);

-- One row per API call: metadata only. The full response is kept only when the
-- call failed. request_params must never contain the API key.
-- Small (~37k rows/day), so it is not partitioned; retention is set in week 2.
CREATE TABLE ops.raw_polls (
    poll_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sweep_id BIGINT NOT NULL REFERENCES ops.sweeps (sweep_id),
    feed TEXT NOT NULL CHECK (feed IN ('bus', 'train')),
    endpoint TEXT NOT NULL,
    request_params JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    latency_ms INTEGER,
    attempts INTEGER NOT NULL DEFAULT 1,
    http_status INTEGER,
    -- Transport failure (timeout, bad JSON) after all retries
    error TEXT,
    -- Per-route errors CTA returns alongside data, e.g. "No data found for parameter"
    api_errors JSONB,
    vehicle_count INTEGER,
    payload JSONB
);

CREATE INDEX raw_polls_sweep_id_idx ON ops.raw_polls (sweep_id);

-- One row per bus sighting (~2.6M/day). Insert-only; the stream CDC ships.
-- Partitioned by UTC day so old days are removed with DROP (no per-row CDC deletes)
-- once the prune gate passes. Keyed on observed_at, the bus's tmstmp resolved to
-- UTC with fetched_at: tmstmp_local repeats in the DST fall-back hour.
CREATE TABLE ops.bus_positions (
    vid TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    tmstmp_local TIMESTAMP NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    rt TEXT NOT NULL,
    pid INTEGER,
    pdist INTEGER,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    hdg INTEGER,
    dly BOOLEAN,
    des TEXT,
    tatripid TEXT,
    tablockid TEXT,
    origtatripno TEXT,
    stst INTEGER,
    stsd DATE,
    psgld TEXT,
    poll_id BIGINT NOT NULL,
    sweep_id BIGINT NOT NULL,
    PRIMARY KEY (vid, observed_at)
) PARTITION BY RANGE (observed_at);

-- Create one daily partition per UTC day from start_day for n_days (idempotent).
-- The poller calls this at startup and hourly so tomorrow's partition always exists.
CREATE FUNCTION ops.ensure_bus_positions_partitions(
    start_day DATE DEFAULT (NOW() AT TIME ZONE 'UTC')::DATE,
    n_days INTEGER DEFAULT 3
) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    d DATE;
BEGIN
    FOR i IN 0 .. n_days - 1 LOOP
        d := start_day + i;
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS ops.%I PARTITION OF ops.bus_positions '
            'FOR VALUES FROM (%L) TO (%L)',
            'bus_positions_' || to_char(d, 'YYYYMMDD'),
            d::TEXT || ' 00:00:00+00',
            (d + 1)::TEXT || ' 00:00:00+00'
        );
    END LOOP;
END;
$$;

-- Current state per bus, for live queries. Derivable from bus_positions, so it is
-- NOT in the CDC publication (as REPLICA IDENTITY FULL it would double the stream).
CREATE TABLE ops.bus_live_board (
    vid TEXT PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    rt TEXT NOT NULL,
    pid INTEGER,
    pdist INTEGER,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    dly BOOLEAN,
    des TEXT,
    stst INTEGER,
    stsd DATE,
    psgld TEXT,
    last_poll_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- CDC (Debezium) reads this publication. Partitions publish as the parent table.
CREATE PUBLICATION ghost_ledger_cdc
FOR TABLE ops.sweeps, ops.raw_polls, ops.bus_positions
WITH (publish_via_partition_root = TRUE);

-- migrate:down
DROP PUBLICATION IF EXISTS ghost_ledger_cdc;
DROP TABLE IF EXISTS ops.bus_live_board;
DROP FUNCTION IF EXISTS ops.ensure_bus_positions_partitions;
DROP TABLE IF EXISTS ops.bus_positions;
DROP TABLE IF EXISTS ops.raw_polls;
DROP TABLE IF EXISTS ops.sweeps;

-- migrate:up
-- Every API response body exactly as received, one row per call (raw_polls holds the
-- call's metadata under the same poll_id). This is the immutable input: every derived
-- table, bus_positions included, can be rebuilt by re-parsing it with fixed code.
-- CTA has no history endpoint, so a field we fail to parse today is
-- otherwise lost for good.
-- Written in the sweep's transaction and published for CDC like everything else.
-- Partitioned by UTC day of fetched_at so the prune gate can DROP old days.
CREATE TABLE ops.raw_responses (
    poll_id BIGINT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (poll_id, fetched_at)
) PARTITION BY RANGE (fetched_at);

-- ~0.8 GB/day of JSON before compression; lz4 is fast and compresses it several-fold.
ALTER TABLE ops.raw_responses ALTER COLUMN body SET COMPRESSION lz4;

-- One function keeps the daily partitions of every day-partitioned table in step.
CREATE FUNCTION ops.ensure_daily_partitions(
    start_day DATE DEFAULT (NOW() AT TIME ZONE 'UTC')::DATE,
    n_days INTEGER DEFAULT 3
) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    parent TEXT;
    d DATE;
BEGIN
    FOREACH parent IN ARRAY ARRAY['bus_positions', 'raw_responses'] LOOP
        FOR i IN 0 .. n_days - 1 LOOP
            d := start_day + i;
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS ops.%I PARTITION OF ops.%I '
                'FOR VALUES FROM (%L) TO (%L)',
                parent || '_' || to_char(d, 'YYYYMMDD'),
                parent,
                d::TEXT || ' 00:00:00+00',
                (d + 1)::TEXT || ' 00:00:00+00'
            );
        END LOOP;
    END LOOP;
END;
$$;

-- The old name stays callable (a poller from the previous image may still call it
-- while the deploy rolls) and now covers both tables.
CREATE OR REPLACE FUNCTION ops.ensure_bus_positions_partitions(
    start_day DATE DEFAULT (NOW() AT TIME ZONE 'UTC')::DATE,
    n_days INTEGER DEFAULT 3
) RETURNS VOID
LANGUAGE sql AS $$
    SELECT ops.ensure_daily_partitions(start_day, n_days);
$$;

SELECT ops.ensure_daily_partitions((NOW() AT TIME ZONE 'UTC')::DATE - 1, 4);

ALTER PUBLICATION ghost_ledger_cdc ADD TABLE ops.raw_responses;

-- migrate:down
DROP TABLE IF EXISTS ops.raw_responses;

CREATE OR REPLACE FUNCTION ops.ensure_bus_positions_partitions(
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

DROP FUNCTION IF EXISTS ops.ensure_daily_partitions;

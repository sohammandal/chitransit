-- System of record for the pollers (DDIA ch. 12-13: one writer, everything
-- else derived from this database's change log via CDC).
-- The poller writes raw_polls and the live board in ONE transaction.

CREATE SCHEMA IF NOT EXISTS ops;

-- One row per API call. Insert-only. request_params must never contain the API key.
CREATE TABLE IF NOT EXISTS ops.raw_polls (
    poll_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed TEXT NOT NULL CHECK (feed IN ('bus', 'train')),
    endpoint TEXT NOT NULL,
    request_params JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    http_status INTEGER,
    error TEXT,
    payload JSONB
);

CREATE INDEX IF NOT EXISTS raw_polls_fetched_at_idx ON ops.raw_polls (fetched_at);

-- Current state per bus. Update only when a field actually changes
-- (WHERE ... IS DISTINCT FROM ...) so CDC emits real changes only.
CREATE TABLE IF NOT EXISTS ops.bus_live_board (
    vid TEXT PRIMARY KEY,
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
    -- As reported by CTA: Chicago local time, no offset (ambiguous in the DST fall-back hour)
    tmstmp_local TIMESTAMP NOT NULL,
    last_poll_id BIGINT NOT NULL REFERENCES ops.raw_polls (poll_id),
    fetched_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Full before-images in the WAL so CDC deletes/updates carry every column.
ALTER TABLE ops.bus_live_board REPLICA IDENTITY FULL;

-- CDC reads this publication (week 2). Add ops.train_live_board when trains arrive.
CREATE PUBLICATION ghost_ledger_cdc FOR TABLE ops.raw_polls, ops.bus_live_board;

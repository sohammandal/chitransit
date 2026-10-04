-- migrate:up
-- Changing tables: facts CTA tells us that get updated or go away. They give CDC
-- real UPDATEs and DELETEs with full before-images, at thousands of rows a day
-- instead of millions.
-- Write only on a real change (INSERT ... ON CONFLICT ... DO UPDATE ... WHERE
-- row IS DISTINCT FROM), so a no-op sweep produces no WAL and no CDC event.

-- Which trip each bus is on. One row per bus currently in service.
--   INSERT: a bus is seen on a trip and has no row
--   UPDATE: the bus starts a different trip (before-image = the trip it left)
--   DELETE: the bus has been missing from the feed for >= 10 minutes (grace period;
--           a single radio dropout must not delete-then-reinsert). The delete is an
--           inference made by the poller, not a fact CTA reported.
-- Sightings without stsd/stst (no trip) do not create or change a row.
CREATE TABLE ops.bus_assignments (
    vid TEXT PRIMARY KEY,
    -- trip_key = (rt, direction, stsd, stst); direction comes from pid -> patterns.rtdir
    rt TEXT NOT NULL,
    direction TEXT NOT NULL,
    stsd DATE NOT NULL,
    stst INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    tablockid TEXT,
    tatripid TEXT,
    origtatripno TEXT,
    -- observed_at of the first sighting on this trip (event time, UTC)
    first_seen_at TIMESTAMPTZ NOT NULL,
    sweep_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX bus_assignments_trip_idx ON ops.bus_assignments (rt, direction, stsd, stst);

-- Current version of every pattern we have seen. getpatterns?rt= lists only the
-- patterns running right now, so new pids are fetched by id on first sight.
--   INSERT: first sight of a pid
--   UPDATE: content_hash changed (detour, reroute); before-image = the old version
--   No DELETE: retired patterns stay; their history lives in Ingress.
-- pattern_versions (SCD2) is built downstream from these change events.
CREATE TABLE ops.patterns (
    pid INTEGER PRIMARY KEY,
    -- getpatterns?pid= does not return the route; the poller knows it from the bus
    rt TEXT NOT NULL,
    rtdir TEXT NOT NULL,
    length_ft DOUBLE PRECISION,
    -- sha256 of the canonical JSON of the API's pattern object
    content_hash TEXT NOT NULL,
    -- the full pattern object as returned (pid, ln, rtdir, pt[], detour fields)
    pattern JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL
);

-- Full before-images on UPDATE and DELETE, so Ingress sees what changed and what left.
ALTER TABLE ops.bus_assignments REPLICA IDENTITY FULL;
ALTER TABLE ops.patterns REPLICA IDENTITY FULL;

ALTER PUBLICATION ghost_ledger_cdc ADD TABLE ops.bus_assignments, ops.patterns;

-- migrate:down
ALTER PUBLICATION ghost_ledger_cdc DROP TABLE ops.bus_assignments, ops.patterns;
DROP TABLE IF EXISTS ops.patterns;
DROP TABLE IF EXISTS ops.bus_assignments;

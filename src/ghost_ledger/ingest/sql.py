"""SQL the poller runs against the changing tables (infra/db/migrations/*_changing_tables.sql).

Every write is conditional: a sweep that sees nothing new must produce no WAL and
therefore no CDC event (tests/test_cdc_events.py checks this with a decoding slot).
"""

# A bus is seen on a trip. INSERT if new; UPDATE only if the trip key changed, so the
# UPDATE's before-image is the trip the bus just left.
UPSERT_ASSIGNMENT = """
INSERT INTO ops.bus_assignments AS a (
    vid, rt, direction, stsd, stst, pid, tablockid, tatripid, origtatripno,
    first_seen_at, sweep_id
)
VALUES (
    %(vid)s, %(rt)s, %(direction)s, %(stsd)s, %(stst)s, %(pid)s, %(tablockid)s,
    %(tatripid)s, %(origtatripno)s, %(first_seen_at)s, %(sweep_id)s
)
ON CONFLICT (vid) DO UPDATE SET
    rt = EXCLUDED.rt,
    direction = EXCLUDED.direction,
    stsd = EXCLUDED.stsd,
    stst = EXCLUDED.stst,
    pid = EXCLUDED.pid,
    tablockid = EXCLUDED.tablockid,
    tatripid = EXCLUDED.tatripid,
    origtatripno = EXCLUDED.origtatripno,
    first_seen_at = EXCLUDED.first_seen_at,
    sweep_id = EXCLUDED.sweep_id,
    updated_at = NOW()
WHERE (a.rt, a.direction, a.stsd, a.stst)
    IS DISTINCT FROM (EXCLUDED.rt, EXCLUDED.direction, EXCLUDED.stsd, EXCLUDED.stst)
"""

# The bus has been missing from the feed for the grace period (an inference).
DELETE_ASSIGNMENT = "DELETE FROM ops.bus_assignments WHERE vid = %(vid)s"

# A pattern was fetched. INSERT if new; UPDATE only if its content hash changed.
UPSERT_PATTERN = """
INSERT INTO ops.patterns AS p (
    pid, rt, rtdir, length_ft, content_hash, pattern, first_seen_at, changed_at
)
VALUES (
    %(pid)s, %(rt)s, %(rtdir)s, %(length_ft)s, %(content_hash)s, %(pattern)s,
    %(seen_at)s, %(seen_at)s
)
ON CONFLICT (pid) DO UPDATE SET
    rtdir = EXCLUDED.rtdir,
    length_ft = EXCLUDED.length_ft,
    content_hash = EXCLUDED.content_hash,
    pattern = EXCLUDED.pattern,
    changed_at = EXCLUDED.changed_at
WHERE p.content_hash IS DISTINCT FROM EXCLUDED.content_hash
"""

# --- Per-sweep writes (all in one transaction; see poller.py) ---

INSERT_SWEEP = """
INSERT INTO ops.sweeps (feed, started_at, finished_at, calls_total, calls_ok, vehicles)
VALUES ('bus', %(started_at)s, %(finished_at)s, %(calls_total)s, %(calls_ok)s, %(vehicles)s)
RETURNING sweep_id
"""

INSERT_RAW_POLL = """
INSERT INTO ops.raw_polls (
    sweep_id, feed, endpoint, request_params, fetched_at, latency_ms, attempts,
    http_status, error, api_errors, vehicle_count, payload
)
VALUES (
    %(sweep_id)s, 'bus', %(endpoint)s, %(request_params)s, %(fetched_at)s, %(latency_ms)s,
    %(attempts)s, %(http_status)s, %(error)s, %(api_errors)s, %(vehicle_count)s, %(payload)s
)
RETURNING poll_id
"""

# The response exactly as received, under the call's poll_id: the replayable input.
INSERT_RAW_RESPONSE = """
INSERT INTO ops.raw_responses (poll_id, fetched_at, body)
VALUES (%(poll_id)s, %(fetched_at)s, %(body)s)
"""

# The same (vid, observed_at) comes back when a bus hasn't reported since the last sweep.
INSERT_POSITION = """
INSERT INTO ops.bus_positions (
    vid, observed_at, tmstmp_local, fetched_at, rt, pid, pdist, lat, lon, hdg, dly, des,
    tatripid, tablockid, origtatripno, stst, stsd, psgld, poll_id, sweep_id
)
VALUES (
    %(vid)s, %(observed_at)s, %(tmstmp_local)s, %(fetched_at)s, %(rt)s, %(pid)s, %(pdist)s,
    %(lat)s, %(lon)s, %(hdg)s, %(dly)s, %(des)s, %(tatripid)s, %(tablockid)s,
    %(origtatripno)s, %(stst)s, %(stsd)s, %(psgld)s, %(poll_id)s, %(sweep_id)s
)
ON CONFLICT (vid, observed_at) DO NOTHING
"""

# Not published for CDC; updated only when the bus reported a new position.
UPSERT_LIVE_BOARD = """
INSERT INTO ops.bus_live_board AS b (
    vid, observed_at, rt, pid, pdist, lat, lon, dly, des, stst, stsd, psgld, last_poll_id
)
VALUES (
    %(vid)s, %(observed_at)s, %(rt)s, %(pid)s, %(pdist)s, %(lat)s, %(lon)s, %(dly)s, %(des)s,
    %(stst)s, %(stsd)s, %(psgld)s, %(poll_id)s
)
ON CONFLICT (vid) DO UPDATE SET
    observed_at = EXCLUDED.observed_at,
    rt = EXCLUDED.rt,
    pid = EXCLUDED.pid,
    pdist = EXCLUDED.pdist,
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    dly = EXCLUDED.dly,
    des = EXCLUDED.des,
    stst = EXCLUDED.stst,
    stsd = EXCLUDED.stsd,
    psgld = EXCLUDED.psgld,
    last_poll_id = EXCLUDED.last_poll_id,
    updated_at = NOW()
WHERE b.observed_at IS DISTINCT FROM EXCLUDED.observed_at
"""

DELETE_LIVE_BOARD = "DELETE FROM ops.bus_live_board WHERE vid = %(vid)s"

# Yesterday through two days ahead (UTC), so late reports near midnight still land.
# Covers every day-partitioned table (bus_positions, raw_responses).
ENSURE_PARTITIONS = "SELECT ops.ensure_daily_partitions(%(today)s::DATE - 1, 4)"

# --- Startup state ---

LOAD_ASSIGNMENTS = "SELECT vid, rt, direction, stsd, stst FROM ops.bus_assignments"

# When each bus was last in a response, repeats included: a bus that keeps sending the same
# stale report is still present (the running poller counts it), but repeats add no
# bus_positions row, so the raw responses are the full record. bus_positions covers any
# time before raw responses were kept. The observed_at bound prunes partitions.
LOAD_LAST_SEEN = """
SELECT DISTINCT ON (vid) vid, rt, fetched_at
FROM (
    SELECT v ->> 'vid' AS vid, v ->> 'rt' AS rt, r.fetched_at
    FROM ops.raw_responses AS r
    INNER JOIN ops.raw_polls AS p ON r.poll_id = p.poll_id
    CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS(
        COALESCE(r.body::JSONB -> 'bustime-response' -> 'vehicle', '[]'::JSONB)
    ) AS v
    WHERE p.endpoint = 'getvehicles' AND r.fetched_at > %(since)s AND p.fetched_at > %(since)s
    UNION ALL
    SELECT vid, rt, fetched_at
    FROM ops.bus_positions
    WHERE fetched_at > %(since)s AND observed_at > %(since)s - INTERVAL '1 hour'
) AS seen
ORDER BY vid, fetched_at DESC
"""

LOAD_PATTERNS = "SELECT pid, rtdir, content_hash FROM ops.patterns"

LOAD_PATTERN_SHAPES = "SELECT pid, rt, rtdir, length_ft, pattern -> 'pt' FROM ops.patterns"

# Recent trails for the live feed's gaps, so a restart doesn't blank them (ghost_ledger.live).
LOAD_TRAILS = """
SELECT vid, observed_at, pid, pdist, stsd, stst, lat, lon
FROM ops.bus_positions
WHERE observed_at > %(since)s AND pid IS NOT NULL AND pdist IS NOT NULL
ORDER BY vid, observed_at
"""

# First and middle stop of every pattern (see poller.sample_stops, which must agree).
LOAD_SAMPLE_STOPS = """
SELECT DISTINCT rt, stpid
FROM (
    SELECT
        p.rt,
        x.pt ->> 'stpid' AS stpid,
        ROW_NUMBER() OVER (PARTITION BY p.pid ORDER BY x.ord) AS i,
        COUNT(*) OVER (PARTITION BY p.pid) AS n
    FROM ops.patterns AS p,
        JSONB_ARRAY_ELEMENTS(p.pattern -> 'pt') WITH ORDINALITY AS x (pt, ord)
    WHERE x.pt ->> 'typ' = 'S'
) AS s
WHERE i = 1 OR i = n / 2 + 1
"""

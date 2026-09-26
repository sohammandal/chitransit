"""SQL the poller runs against the changing tables (db/migrations/*_changing_tables.sql).

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

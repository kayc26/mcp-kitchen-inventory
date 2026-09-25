"""One-off: give existing rows with a made_date but no expiry an expiry of
made_date + 4 days, matching what add_item now does for new leftovers.

Safe to re-run; rows that already have an expiry are never touched.

    uv run --env-file .env.local scripts/backfill_leftover_expiry.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import server

with server._get_conn() as conn:
    changed = server._backfill_leftover_expiry(conn)
print(f"Backfilled expiry on {changed} row(s).")

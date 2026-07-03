"""One-off backfill: delete Bigtable rows for already-tombstoned predictions.

The validator now deletes Bigtable rows when it soft-deletes the Postgres
siblings (density tapering / cleanup_old_history), but rows tombstoned
before that change keep their blobs until the per-table GC max-age. This
script deletes those orphaned blobs.

Run with the SAME environment as the validator — Postgres creds
(POSTGRES_USER/PASSWORD/HOST/PORT/DB) and, for --execute, Bigtable
(BIGTABLE_PROJECT, BIGTABLE_INSTANCE, BIGTABLE_TABLE_PREDICTION_LOW,
BIGTABLE_TABLE_PREDICTION_HIGH) + GOOGLE_APPLICATION_CREDENTIALS.

Dry-run by default: only counts the tombstoned rows that still carry a
bigtable_key, per time_length. Pass --execute to actually delete.
Idempotent — deleting an already-deleted Bigtable row is a no-op, so the
script can be re-run after a partial failure.

    python scripts/backfill_bigtable_tombstone_deletes.py
    python scripts/backfill_bigtable_tombstone_deletes.py --execute
"""

import argparse
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import text

from synth.db.models import get_engine
from synth.validator.bigtable_prediction_storage import (
    BigtablePredictionStorage,
)

PAGE_SIZE = 10_000

# Paginated by mp.id so memory stays bounded on a large backlog.
_PAGE_SQL = text("""
    SELECT mp.id, mp.bigtable_key, vr.time_length
      FROM miner_predictions mp
      JOIN validator_requests vr ON vr.id = mp.validator_requests_id
     WHERE mp.deleted_at IS NOT NULL
       AND mp.bigtable_key IS NOT NULL
       AND mp.id > :last_id
     ORDER BY mp.id
     LIMIT :page_size
""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="delete Bigtable rows of tombstoned miner_predictions"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually delete from Bigtable (default: dry-run, count only)",
    )
    args = parser.parse_args()

    load_dotenv()
    engine = get_engine()
    storage = BigtablePredictionStorage() if args.execute else None

    counts_per_time_length: dict[int, int] = defaultdict(int)
    last_id = 0
    while True:
        with engine.connect() as connection:
            rows = connection.execute(
                _PAGE_SQL, {"last_id": last_id, "page_size": PAGE_SIZE}
            ).all()
        if not rows:
            break
        last_id = rows[-1][0]

        keys_per_time_length: dict[int, list[str]] = defaultdict(list)
        for _, bigtable_key, time_length in rows:
            keys_per_time_length[time_length].append(bigtable_key)

        for time_length, keys in keys_per_time_length.items():
            if storage is not None:
                storage.delete_predictions(time_length, keys)
            counts_per_time_length[time_length] += len(keys)

        total = sum(counts_per_time_length.values())
        print(f"processed {total} rows (last id {last_id})")

    action = "deleted" if args.execute else "would delete (dry-run)"
    for time_length, count in sorted(counts_per_time_length.items()):
        print(f"time_length={time_length}: {action} {count} bigtable rows")
    if not counts_per_time_length:
        print("no tombstoned rows with a bigtable_key found")


if __name__ == "__main__":
    main()

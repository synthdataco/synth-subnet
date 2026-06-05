"""One-time migration: shrink miner_scores.score_details_v3.crps_data.

Historical rows stored a verbose ``crps_data`` list with one entry per scoring
increment (``Increment`` = 1, 2, 3, ...) alongside the per-interval ``Total``
rows. PR #273 changed the live writer (``crps_calculation.py``) to keep only:

  * one ``Total`` row per scoring interval,
  * an aggregate ``{"Interval": "Gaps", "Increment": "Total"}`` row (when any
    ``*_gaps`` interval scored), and
  * the final ``{"Interval": "Overall", "Increment": "Total"}`` row.

This rewrites the old rows into that shape so the column is uniform and far
smaller. The per-increment detail is intentionally discarded.

The aggregate gap total is reconstructed by summing the per-interval ``Total``
of every interval whose name ends with ``_gaps`` — the same value the new
writer accumulates, and all that survives in already-trimmed rows.

Idempotent: re-running recomputes the identical list and skips the write.
Dry run by default; pass ``--apply`` to persist.
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv
from sqlalchemy import bindparam, select, update

from synth.db.models import MinerScore, get_engine

load_dotenv()


def trim_crps_data(crps_data: list[dict]) -> list[dict]:
    """Reduce a verbose ``crps_data`` list to the post-#273 shape.

    Keeps the per-interval ``Total`` rows, appends a reconstructed
    ``Gaps``/``Total`` aggregate, then the ``Overall``/``Total`` row — matching
    the order the live writer produces. Error payloads (e.g.
    ``[{"error": ...}]``) and anything without ``Total`` rows are left as-is.
    """
    totals = [d for d in crps_data if d.get("Increment") == "Total"]
    if not totals:
        return crps_data

    # Drop any existing aggregate so a re-run recomputes from scratch (keeps
    # the function idempotent).
    totals = [d for d in totals if d.get("Interval") != "Gaps"]

    gap_total = sum(
        d["CRPS"]
        for d in totals
        if str(d.get("Interval", "")).endswith("_gaps")
    )

    overall = [d for d in totals if d.get("Interval") == "Overall"]
    trimmed = [d for d in totals if d.get("Interval") != "Overall"]
    if gap_total > 0:
        trimmed.append(
            {"Interval": "Gaps", "Increment": "Total", "CRPS": gap_total}
        )
    trimmed.extend(overall)
    return trimmed


def main(apply: bool, batch_size: int, after_id: int) -> None:
    engine = get_engine()
    scanned = 0
    changed = 0

    while True:
        with engine.begin() as connection:
            rows = connection.execute(
                select(MinerScore.id, MinerScore.score_details_v3)
                .where(MinerScore.id > after_id)
                .order_by(MinerScore.id)
                .limit(batch_size)
            ).all()
            if not rows:
                print("no more rows to scan")
                break

            updates = []
            for row in rows:
                details = row.score_details_v3
                if not isinstance(details, dict):
                    continue
                crps_data = details.get("crps_data")
                if crps_data is None:
                    continue
                trimmed = trim_crps_data(crps_data)
                if trimmed == crps_data:
                    continue
                updates.append((row.id, {**details, "crps_data": trimmed}))

            if apply and updates:
                # executemany: one round-trip per batch, not per row.
                connection.execute(
                    update(MinerScore)
                    .where(MinerScore.id == bindparam("b_id"))
                    .values(score_details_v3=bindparam("b_details")),
                    [
                        {"b_id": score_id, "b_details": new_details}
                        for score_id, new_details in updates
                    ],
                )

        scanned += len(rows)
        changed += len(updates)
        after_id = rows[-1].id
        print(
            f"trim progress: scanned={scanned} changed={changed} "
            f"(last id={after_id})"
        )

    verb = "updated" if apply else "would update (dry run)"
    print(f"trim done: {verb} {changed}/{scanned} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Trim miner_scores.score_details_v3 crps_data to Totals."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist changes (default: dry run, no writes)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="rows scanned per id-keyset batch (default: 1000)",
    )
    parser.add_argument(
        "--after-id",
        type=int,
        default=0,
        help="start scanning after this ID (default: 0)",
    )
    args = parser.parse_args()
    main(apply=args.apply, batch_size=args.batch_size, after_id=args.after_id)

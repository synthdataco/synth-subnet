"""One-time migration: shrink miner_scores.score_details_v3.crps_data.

Historical rows stored a verbose ``crps_data`` list with one entry per scoring
increment (``Increment`` = 1, 2, 3, ...). This trims each row down to:

  * one ``Total`` row per *regular* scoring interval,
  * a single aggregate ``{"Interval": "Gaps", "Increment": "Total"}`` row that
    sums the per-interval ``*_gaps`` totals — the individual ``*_gaps``
    interval totals are dropped, and
  * the final ``{"Interval": "Overall", "Increment": "Total"}`` row.

Re-runnable from id 0 across every shape we have written:

  * verbose rows (per-increment entries, no Gaps aggregate),
  * rows trimmed by the previous version (regular totals + the individual
    ``*_gaps`` totals + a Gaps aggregate), and
  * rows already in the aggregate-only shape above.

When the individual ``*_gaps`` totals are still present the Gaps aggregate is
recomputed from them and they are dropped; once they are gone the existing
``Gaps`` row is preserved verbatim, so a re-run is a no-op and skips the write.

Dry run by default; pass ``--apply`` to persist. The equivalent server-side
SQL (function + batched procedure) lives in ``TRIM_SQL`` below — far faster on
a large table since no rows cross the wire.
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv
from sqlalchemy import bindparam, select, update

from synth.db.models import MinerScore, get_engine

load_dotenv()


def trim_crps_data(crps_data: list[dict]) -> list[dict]:
    """Reduce a ``crps_data`` list to regular-interval Totals + one Gaps
    aggregate + Overall.

    Drops every per-increment row and every individual ``*_gaps`` interval
    Total, replacing the latter with a single ``Gaps``/``Total`` aggregate.
    Error payloads (e.g. ``[{"error": ...}]``) and anything without ``Total``
    rows are left as-is.

    The aggregate is recomputed from the individual ``*_gaps`` totals when they
    are present (verbose rows, and rows trimmed by the previous version); once
    they are gone the existing ``Gaps`` row is kept verbatim, so a re-run from
    id 0 is a no-op.
    """
    totals = [d for d in crps_data if d.get("Increment") == "Total"]
    if not totals:
        return crps_data

    def _is_gap_interval(d: dict) -> bool:
        return str(d.get("Interval", "")).endswith("_gaps")

    # Regular per-interval totals: not a gap interval, the Gaps aggregate, or
    # Overall. Original order preserved.
    body = [
        d
        for d in totals
        if not _is_gap_interval(d)
        and d.get("Interval") != "Gaps"
        and d.get("Interval") != "Overall"
    ]
    overall = [d for d in totals if d.get("Interval") == "Overall"]

    gap_intervals = [d for d in totals if _is_gap_interval(d)]
    if gap_intervals:
        # Recompute from the individual *_gaps totals and drop them (covers
        # verbose rows and rows trimmed by the previous version).
        gap_total = sum(d["CRPS"] for d in gap_intervals)
        gaps = (
            [{"Interval": "Gaps", "Increment": "Total", "CRPS": gap_total}]
            if gap_total > 0
            else []
        )
    else:
        # Already aggregate-only: keep the existing Gaps row verbatim so a
        # re-run from id 0 does not strip it.
        gaps = [d for d in totals if d.get("Interval") == "Gaps"]

    return body + gaps + overall


# Server-side equivalent of trim_crps_data, kept in sync with it. Running this
# in Postgres is dramatically faster than the row-by-row Python path because no
# data crosses the wire. Install the function + procedure once, then
# ``CALL run_trim_score_details_v3(10000);`` from a non-atomic context (i.e.
# autocommit on / no surrounding transaction; otherwise use trim_batch in a
# client-side loop).
TRIM_SQL = r"""
CREATE OR REPLACE FUNCTION trim_crps_data(crps_data jsonb)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
AS $fn$
DECLARE
    body          jsonb;
    overall       jsonb;
    existing_gaps jsonb;
    gaps          jsonb;
    n_gaps        int;
    gap_total     double precision;
BEGIN
    -- Leave non-arrays and error payloads (no "Total" rows) untouched.
    IF crps_data IS NULL
       OR jsonb_typeof(crps_data) <> 'array'
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(crps_data) e
                      WHERE e->>'Increment' = 'Total')
    THEN
        RETURN crps_data;
    END IF;

    -- regular per-interval totals: not a gap interval, the Gaps aggregate, or
    -- Overall; original order preserved
    SELECT COALESCE(jsonb_agg(e ORDER BY ord), '[]'::jsonb) INTO body
    FROM jsonb_array_elements(crps_data) WITH ORDINALITY AS t(e, ord)
    WHERE e->>'Increment' = 'Total'
      AND right(e->>'Interval', 5) <> '_gaps'
      AND e->>'Interval' <> 'Gaps'
      AND e->>'Interval' <> 'Overall';

    SELECT COALESCE(jsonb_agg(e ORDER BY ord), '[]'::jsonb) INTO overall
    FROM jsonb_array_elements(crps_data) WITH ORDINALITY AS t(e, ord)
    WHERE e->>'Increment' = 'Total'
      AND e->>'Interval' = 'Overall';

    SELECT count(*), COALESCE(SUM((e->>'CRPS')::double precision), 0)
      INTO n_gaps, gap_total
    FROM jsonb_array_elements(crps_data) e
    WHERE e->>'Increment' = 'Total'
      AND right(e->>'Interval', 5) = '_gaps';

    IF n_gaps > 0 THEN
        -- recompute the aggregate from the individual *_gaps totals, drop them
        gaps := CASE WHEN gap_total > 0
                     THEN jsonb_build_array(jsonb_build_object(
                            'Interval', 'Gaps',
                            'Increment', 'Total',
                            'CRPS', gap_total))
                     ELSE '[]'::jsonb END;
    ELSE
        -- already aggregate-only: preserve the existing Gaps row verbatim so a
        -- re-run from id 0 is a no-op
        SELECT e INTO existing_gaps
        FROM jsonb_array_elements(crps_data) e
        WHERE e->>'Increment' = 'Total' AND e->>'Interval' = 'Gaps'
        LIMIT 1;
        gaps := CASE WHEN existing_gaps IS NOT NULL
                     THEN jsonb_build_array(existing_gaps)
                     ELSE '[]'::jsonb END;
    END IF;

    RETURN body || gaps || overall;
END;
$fn$;

-- Batched runner: one COMMIT per id-range. Must be CALLed in a non-atomic
-- context (autocommit on, no enclosing transaction) or the COMMIT raises
-- "invalid transaction termination".
CREATE OR REPLACE PROCEDURE run_trim_score_details_v3(
    batch_size bigint DEFAULT 10000
)
LANGUAGE plpgsql AS $proc$
DECLARE
    lo     bigint := 0;
    max_id bigint;
BEGIN
    SELECT max(id) INTO max_id FROM miner_scores;
    WHILE lo <= max_id LOOP
        UPDATE miner_scores m
        SET score_details_v3 =
                jsonb_set(m.score_details_v3, '{crps_data}', t.trimmed)
        FROM (
            SELECT id, trim_crps_data(score_details_v3->'crps_data') AS trimmed
            FROM miner_scores
            WHERE id > lo AND id <= lo + batch_size
              AND score_details_v3 ? 'crps_data'
        ) t
        WHERE m.id = t.id
          AND m.score_details_v3->'crps_data' IS DISTINCT FROM t.trimmed;
        COMMIT;
        RAISE NOTICE 'trim done up to id %', lo + batch_size;
        lo := lo + batch_size;
    END LOOP;
END;
$proc$;

-- Alternative for clients that cannot provide a non-atomic context (e.g. a
-- transaction-wrapping GUI): trims one id-range and returns the rows changed.
-- Drive the batching from the client, advancing the bounds each call:
--   SELECT trim_batch(0, 10000);
CREATE OR REPLACE FUNCTION trim_batch(lo bigint, hi bigint)
RETURNS bigint LANGUAGE sql AS $batch$
    WITH upd AS (
        UPDATE miner_scores m
        SET score_details_v3 =
                jsonb_set(m.score_details_v3, '{crps_data}', t.trimmed)
        FROM (
            SELECT id, trim_crps_data(score_details_v3->'crps_data') AS trimmed
            FROM miner_scores
            WHERE id > lo AND id <= hi
              AND score_details_v3 ? 'crps_data'
        ) t
        WHERE m.id = t.id
          AND m.score_details_v3->'crps_data' IS DISTINCT FROM t.trimmed
        RETURNING 1
    )
    SELECT count(*) FROM upd;
$batch$;
"""


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

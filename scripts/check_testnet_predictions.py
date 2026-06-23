"""Read-only check: do we have (valid) miner predictions per competition?

Run with the SAME environment as the validator — Postgres creds
(POSTGRES_USER/PASSWORD/HOST/PORT/DB), Bigtable (BIGTABLE_PROJECT,
BIGTABLE_INSTANCE, BIGTABLE_TABLE_PREDICTION_LOW, BIGTABLE_TABLE_PREDICTION_HIGH),
GOOGLE_APPLICATION_CREDENTIALS, and PYTHONPATH=. — so it points at the
same testnet PG + Bigtable instance the validator writes to.

Read-only: it only SELECTs from Postgres and range-reads Bigtable; it never
writes or deletes. For each asset seen in the last LOOKBACK_HOURS it reports
how many validator_requests exist, and (by hydrating a few recent ones from
Bigtable) how many predictions arrived and how many are valid.

    python verify/check_testnet_predictions.py
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from synth.db.models import ValidatorRequest
from synth.validator import competition_config, response_validation_v2
from synth.validator.bigtable_prediction_storage import (
    BigtablePredictionStorage,
)
from synth.validator.miner_data_handler import MinerDataHandler

LOOKBACK_HOURS = 6
# How many recent requests per (asset, time_length) to hydrate from Bigtable.
SAMPLE_PER_GROUP = 3


def _asset_to_competition() -> dict[tuple[str, int], str]:
    # Keyed on (asset, time_length): the same asset (e.g. BTC) belongs to both
    # CRYPTO_1H and CRYPTO_24H, so the time_length is what disambiguates them.
    mapping: dict[tuple[str, int], str] = {}
    for comp in competition_config.ALL_COMPETITIONS:
        for asset in comp.asset_list:
            mapping.setdefault((asset, comp.time_length), comp.label)
    return mapping


def main() -> None:
    load_dotenv()
    handler = MinerDataHandler(bigtable_storage=BigtablePredictionStorage())
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    asset_comp = _asset_to_competition()

    with Session(handler.engine) as session:
        requests = (
            session.execute(
                select(ValidatorRequest)
                .where(ValidatorRequest.start_time > since)
                .order_by(ValidatorRequest.start_time.desc())
            )
            .scalars()
            .all()
        )

        by_group: dict[tuple, list] = defaultdict(list)
        for vr in requests:
            by_group[(vr.asset, vr.time_length)].append(vr)

        print(
            f"validator_requests in the last {LOOKBACK_HOURS}h: "
            f"{len(requests)} across {len(by_group)} (asset, time_length) groups"
        )
        header = (
            f"\n{'asset':8} {'time_len':>8} {'competition':26} "
            f"{'#req':>5} {'#pred':>7} {'#valid':>7}  sample_price"
        )
        print(header)
        print("-" * len(header))

        for (asset, time_length), reqs in sorted(by_group.items()):
            comp = asset_comp.get(
                (asset, time_length), "(not in any competition)"
            )
            n_pred = n_valid = 0
            sample_price = ""
            for vr in reqs[:SAMPLE_PER_GROUP]:
                preds = handler.get_predictions_by_request(vr) or []
                n_pred += len(preds)
                for p in preds:
                    if (
                        p.format_validation == response_validation_v2.CORRECT
                        and p.prediction
                        and len(p.prediction) > 2
                        and p.prediction[2]
                    ):
                        n_valid += 1
                        if not sample_price:
                            # prediction = [start_ts, increment, [path], ...]
                            sample_price = p.prediction[2][0]
            print(
                f"{asset:8} {time_length:>8} {comp:26} "
                f"{len(reqs):>5} {n_pred:>7} {n_valid:>7}  {sample_price}"
            )


if __name__ == "__main__":
    main()

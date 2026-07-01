from datetime import datetime, timedelta
from types import SimpleNamespace
import typing
import logging
import math
import os


import bittensor as bt
import pandas as pd
from sqlalchemy import (
    join,
    literal_column,
    Connection,
    Engine,
    and_,
    exists,
    select,
    func,
    desc,
    not_,
    update,
    delete,
    text,
)
from sqlalchemy.dialects.postgresql import insert
from tenacity import (
    before_log,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)


from synth.db.models import (
    MinerPrediction,
    Miner,
    MinerScore,
    ValidatorRequest,
    MetagraphHistory,
    MinerReward,
    get_engine,
    WeightsUpdateHistory,
)
from synth.simulation_input import SimulationInput
from synth.utils.logging import print_execution_time
from synth.validator import prompt_config, response_validation_v2
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator.storage_backend import (
    BIGTABLE_MISSING_FORMAT,
    BIGTABLE_SENTINEL,
)

if typing.TYPE_CHECKING:
    # Imported only for type hints. The Bigtable storage module pulls in
    # google-cloud-bigtable, which is optional for validators that stay on
    # the Postgres backend.
    from synth.validator.bigtable_prediction_storage import (
        BigtablePredictionStorage,
    )

# Observed headroom for Pyth to index the candle that opens at the end of
# the prediction window. Combined with one full candle interval below, it
# decides when a request becomes eligible for scoring. Tune via logs of
# `realized path not yet settled` warnings — if those fire repeatedly,
# Pyth's tail latency exceeds this and the value should grow.
PYTH_PUBLISH_LATENCY_SECONDS = 30

# Scoring gate = the candle interval that the settlement guard in
# PriceDataProvider looks past + headroom for Pyth to publish that witness
# candle. Must be >= PriceDataProvider.CANDLE_INTERVAL_SECONDS or the
# guard inside fetch_data will fail every first attempt and rely on retry.
SCORING_GATE_SECONDS = (
    PriceDataProvider.CANDLE_INTERVAL_SECONDS + PYTH_PUBLISH_LATENCY_SECONDS
)


class MinerDataHandler:
    def __init__(
        self,
        engine: typing.Optional[Engine] = None,
        bigtable_storage: typing.Optional["BigtablePredictionStorage"] = None,
    ):
        # Use the provided engine or fall back to the default engine
        self.engine = engine or get_engine()
        # When set, prediction payloads are shipped to Bigtable and the
        # Postgres `prediction` column holds a sentinel + `bigtable_key`.
        self.bigtable_storage = bigtable_storage
        # Optional salt for density-tapering keeper selection. The kept
        # validator_request per bucket is picked by md5(id || salt), so the
        # keeper is spread across the bucket rather than always its earliest
        # member. Unset falls back to md5(id); warn once so that's not
        # silent.
        self.thinning_salt = os.getenv("THINNING_SALT", "")
        if not self.thinning_salt:
            bt.logging.warning(
                "THINNING_SALT is not set; density-tapering keeper "
                "selection falls back to md5(id)."
            )

    def get_miner_uids(self, connection: Connection):
        ranked_miners = select(
            Miner,
            func.row_number()
            .over(
                partition_by=Miner.miner_uid,
                order_by=desc(Miner.updated_at),
            )
            .label("rn"),
        ).alias("ranked_miners")
        query = select(ranked_miners.c.id, ranked_miners.c.miner_uid).where(
            ranked_miners.c.rn == 1
        )
        return connection.execute(query)

    def get_miner_uids_map(self, connection: Connection):
        miners = self.get_miner_uids(connection)

        # map miner_uid -> miner_id
        miner_id_map = {}
        for row in miners:
            miner_id_map[row.miner_uid] = row.id

        return miner_id_map

    def get_miner_ids_map(self, connection: Connection):
        miners = self.get_miner_uids(connection)

        # map miner_id -> miner_uid
        miner_Uid_map = {}
        for row in miners:
            miner_Uid_map[row.id] = row.miner_uid

        return miner_Uid_map

    def get_latest_asset(self, time_length: int) -> str | None:
        try:
            with self.engine.connect() as connection:
                query = (
                    select(
                        ValidatorRequest.asset,
                    )
                    .where(ValidatorRequest.time_length == time_length)
                    .limit(1)
                    .order_by(ValidatorRequest.start_time.desc())
                )

                result = connection.execute(query).fetchall()
                if len(result) == 0:
                    return None

                return str(result[0].asset)
        except Exception as e:
            bt.logging.exception(f"in get_next_asset (got an exception): {e}")
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=2),
        reraise=True,
        before=before_log(bt.logging._logger, logging.DEBUG),
    )
    @print_execution_time
    def save_responses(
        self,
        miner_predictions: dict,
        simulation_input: SimulationInput,
        request_time: datetime,
    ):
        """Save miner predictions and simulation input.

        When `self.bigtable_storage` is set, CORRECT predictions are uploaded
        to Bigtable first; the Postgres `prediction` column then carries a
        sentinel JSON and `bigtable_key` carries the row key.
        """

        # Prepare the ValidatorRequest row from the simulation input:
        validator_requests_row = {
            "start_time": simulation_input.start_time,
            "asset": simulation_input.asset,
            "time_increment": simulation_input.time_increment,
            "time_length": simulation_input.time_length,
            "num_simulations": simulation_input.num_simulations,
            "request_time": request_time.isoformat(),
        }

        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    # Insert into ValidatorRequest and get its ID
                    insert_stmt_validator = insert(ValidatorRequest).values(
                        validator_requests_row
                    )
                    result = connection.execute(insert_stmt_validator)
                    validator_requests_id = result.inserted_primary_key[0]

                    # Create the records to insert
                    miner_id_map = self.get_miner_uids_map(connection)

                    # Ship prediction blobs to Bigtable before we open the
                    # Postgres write loop. If Postgres later fails the
                    # @retry wrapper above will replay, and orphan Bigtable
                    # rows age out via per-table GC policy.
                    bigtable_keys: dict = {}
                    if self.bigtable_storage is not None:
                        bigtable_keys = (
                            self.bigtable_storage.write_predictions(
                                simulation_input=simulation_input,
                                miner_predictions=miner_predictions,
                                miner_id_map=miner_id_map,
                            )
                        )

                    miner_prediction_records = []

                    for miner_uid, (
                        prediction,
                        format_validation,
                        process_time,
                    ) in miner_predictions.items():
                        if miner_uid not in miner_id_map:
                            bt.logging.error(
                                f"in save_responses, miner_uid {miner_uid} not found in miners table"
                            )
                            continue
                        miner_id = miner_id_map[miner_uid]

                        is_correct = (
                            format_validation == response_validation_v2.CORRECT
                        )
                        bigtable_key = bigtable_keys.get(miner_uid)
                        if self.bigtable_storage is not None and is_correct:
                            # Invariant: write_predictions returns a key for
                            # every (CORRECT, known miner_uid) pair, and
                            # raises on any mutate failure. A CORRECT row
                            # reaching this branch without a key would mean
                            # the storage class drifted from the contract —
                            # fail loudly rather than write a sentinel that
                            # points at nothing.
                            if bigtable_key is None:
                                raise RuntimeError(
                                    f"bigtable storage returned no key for "
                                    f"miner_uid {miner_uid} despite CORRECT "
                                    f"format_validation"
                                )
                            prediction_column: typing.Any = BIGTABLE_SENTINEL
                        elif is_correct:
                            prediction_column = prediction
                        else:
                            prediction_column = []

                        miner_prediction_records.append(
                            {
                                "validator_requests_id": validator_requests_id,
                                "miner_uid": miner_uid,  # deprecated
                                "miner_id": miner_id,
                                "prediction": prediction_column,
                                "bigtable_key": bigtable_key,
                                "format_validation": format_validation,
                                "process_time": process_time,
                            }
                        )

                    # 4. Insert into miners table
                    if len(miner_prediction_records) == 0:
                        return None
                    insert_stmt_miner_predictions = insert(
                        MinerPrediction
                    ).values(miner_prediction_records)
                    connection.execute(insert_stmt_miner_predictions)
            return validator_requests_id  # TODO: finish this: refactor to add the validator_requests_id in the score and reward table
        except Exception as e:
            bt.logging.exception(f"in save_responses (got an exception): {e}")
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=7),
        reraise=True,
        before=before_log(bt.logging._logger, logging.DEBUG),
    )
    @print_execution_time
    def set_miner_scores(
        self,
        real_prices: list[dict],
        validator_requests_id: int,
        reward_details: list[dict],
        scored_time: datetime,
    ):
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    # update validator request with the real paths
                    if real_prices is not None and len(real_prices) > 0:
                        real_prices = [
                            (
                                None
                                if (isinstance(x, float) and math.isnan(x))
                                else x
                            )
                            for x in real_prices
                        ]
                        update_stmt_validator = (
                            update(ValidatorRequest)
                            .where(
                                ValidatorRequest.id == validator_requests_id
                            )
                            .values(
                                {
                                    "real_prices": real_prices,
                                }
                            )
                        )
                        connection.execute(update_stmt_validator)

                    rows_to_insert = []
                    for row in reward_details:
                        rows_to_insert.append(
                            {
                                "miner_uid": row["miner_uid"],  # deprecated
                                "scored_time": scored_time.isoformat(),
                                "miner_predictions_id": row[
                                    "miner_prediction_id"
                                ],
                                "score_details_v3": {
                                    "total_crps": row["total_crps"],
                                    "percentile90": row["percentile90"],
                                    "lowest_score": row["lowest_score"],
                                    "prompt_score_v3": row["prompt_score_v3"],
                                    "crps_data": row["crps_data"],
                                },
                                "prompt_score_v3": row["prompt_score_v3"],
                            }
                        )
                    stmt = insert(MinerScore).values(rows_to_insert)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_miner_scores_miner_predictions_id",
                        set_={
                            "score_details_v3": stmt.excluded.score_details_v3,
                            "prompt_score_v3": stmt.excluded.prompt_score_v3,
                        },
                    )
                    connection.execute(stmt)
        except Exception as e:
            bt.logging.exception(
                f"in set_miner_scores (got an exception): {e}"
            )

    def get_miner_prediction(
        self, miner_uid: int, validator_request_id: int
    ) -> typing.Optional[MinerPrediction]:
        """Retrieve the record with the longest valid interval for the given miner_id."""
        try:
            with self.engine.connect() as connection:
                query = (
                    select(
                        MinerPrediction.id,
                        MinerPrediction.prediction,
                        MinerPrediction.format_validation,
                        MinerPrediction.process_time,
                    )
                    .select_from(MinerPrediction)
                    .join(
                        Miner,
                        Miner.id == MinerPrediction.miner_id,
                    )
                    .where(
                        Miner.miner_uid == miner_uid,
                        MinerPrediction.validator_requests_id
                        == validator_request_id,
                    )
                    .limit(1)
                )

                result = MinerPrediction()
                row = connection.execute(query).fetchone()
                if row is not None:
                    result.id = row.id
                    result.prediction = row.prediction
                    result.format_validation = row.format_validation
                    result.process_time = row.process_time

            return result
        except Exception as e:
            bt.logging.exception(
                f"in get_miner_prediction (got an exception): {e}"
            )
            return None

    @print_execution_time
    def get_predictions_by_request(
        self, validator_request
    ) -> typing.Optional[list]:
        """Return all miner predictions for `validator_request`.

        Takes the full `validator_request` row (not just its id) so we avoid
        a second DB roundtrip when hydrating Bigtable-backed predictions —
        the caller already has the row from `get_validator_requests_to_score`.

        Each returned item has the attributes `miner_uid`, `id`, `prediction`,
        `format_validation`, `process_time`. Postgres-only rows are returned
        as SQLAlchemy `Row` objects (with an extra `bigtable_key` attribute,
        unused by consumers); Bigtable-hydrated rows are returned as
        `SimpleNamespace` with the same five attributes. Consumers
        (`reward.py`) access by name and are agnostic to which one they get.
        """
        try:
            with self.engine.connect() as connection:
                query = (
                    select(
                        Miner.miner_uid,
                        MinerPrediction.id,
                        MinerPrediction.prediction,
                        MinerPrediction.format_validation,
                        MinerPrediction.process_time,
                        MinerPrediction.bigtable_key,
                    )
                    .select_from(MinerPrediction)
                    .join(
                        Miner,
                        Miner.id == MinerPrediction.miner_id,
                    )
                    .where(
                        and_(
                            MinerPrediction.validator_requests_id
                            == validator_request.id,
                            # Soft-deleted rows (cleanup_old_history or
                            # density_tapering_predictions) carry tombstone
                            # payloads — never feed them to the scorer.
                            MinerPrediction.deleted_at.is_(None),
                        ),
                    )
                )

                rows = connection.execute(query).fetchall()

            bigtable_rows = [r for r in rows if r.bigtable_key is not None]
            if not bigtable_rows:
                return list(rows)

            return self._hydrate_from_bigtable(
                validator_request, rows, bigtable_rows
            )
        except Exception as e:
            bt.logging.exception(
                f"in get_predictions_by_request (got an exception): {e}"
            )
            return None

    def _hydrate_from_bigtable(
        self, validator_request, rows: list, bigtable_rows: list
    ) -> list:
        """Replace Bigtable-backed sentinels with the actual prediction.

        `rows` is the full result set from the Postgres query above (mix of
        Postgres-only and Bigtable-backed). `bigtable_rows` is the subset
        with `bigtable_key IS NOT NULL` — passed in so we don't filter twice.
        """
        if self.bigtable_storage is None:
            # Bigtable-backed rows can't be hydrated without the storage
            # client. Treating them as missing would silently mis-score
            # every miner whose prediction lives in Bigtable, so fail the
            # read instead and let the caller surface it.
            raise RuntimeError(
                "found bigtable-backed predictions but no bigtable storage "
                "is configured on this validator"
            )
        paths_by_key = self.bigtable_storage.read_predictions(
            validator_request,
            [r.bigtable_key for r in bigtable_rows],
        )

        start_ts = int(validator_request.start_time.timestamp())
        time_increment = int(validator_request.time_increment)

        hydrated = []
        for r in rows:
            if r.bigtable_key is None:
                hydrated.append(r)
                continue
            paths = paths_by_key.get(r.bigtable_key) or []
            if paths:
                prediction = [start_ts, time_increment, *paths]
                format_validation = r.format_validation
            else:
                # Bigtable row missing or undecodable. Flip format_validation away from CORRECT
                prediction = []
                format_validation = BIGTABLE_MISSING_FORMAT
            hydrated.append(
                SimpleNamespace(
                    miner_uid=r.miner_uid,
                    id=r.id,
                    prediction=prediction,
                    format_validation=format_validation,
                    process_time=r.process_time,
                )
            )

        return hydrated

    @print_execution_time
    def get_validator_requests_to_score(
        self,
        scored_time: datetime,
        window_days: int,
        time_length: int,
        asset_list: list[str],
    ) -> typing.Optional[list[ValidatorRequest]]:
        """
        Retrieve the list of IDs of the latest validator requests that (start_time + time_length) < scored_time
        and (start_time + time_length) >= scored_time - window_days.
        This is to ensure that we only get requests that are within the window_days.
        and exclude records that are already scored
        """
        try:
            with self.engine.connect() as connection:
                subq = (
                    select(1)
                    .select_from(
                        join(
                            MinerPrediction,
                            MinerScore,
                            MinerPrediction.id
                            == MinerScore.miner_predictions_id,
                        )
                    )
                    .where(
                        and_(
                            MinerPrediction.validator_requests_id
                            == ValidatorRequest.id,
                            MinerScore.prompt_score_v3.isnot(None),
                        )
                    )
                )

                # Density tapering soft-deletes every prediction on
                # redundant validator_requests (start_time older than
                # `thin_after_minutes`) well before they reach scoring
                # eligibility. Without this guard, those tombstoned
                # requests would still surface here once their
                # `start_time + time_length + SCORING_GATE_SECONDS`
                # passes, and `_crps_worker` would score `{"deleted":
                # true}` payloads as garbage.
                alive_subq = (
                    select(1)
                    .select_from(MinerPrediction)
                    .where(
                        and_(
                            MinerPrediction.validator_requests_id
                            == ValidatorRequest.id,
                            MinerPrediction.deleted_at.is_(None),
                        )
                    )
                )

                window_start = (
                    ValidatorRequest.start_time
                    + literal_column("INTERVAL '1 second'")
                    * ValidatorRequest.time_length
                    # Wait one full candle interval past window-end so the
                    # last candle has closed, plus headroom for Pyth to
                    # publish the witness candle that PriceDataProvider's
                    # settlement guard checks for. See SCORING_GATE_SECONDS.
                    + literal_column("INTERVAL '1 second'")
                    * SCORING_GATE_SECONDS
                )

                query = (
                    select(
                        ValidatorRequest.id,
                        ValidatorRequest.start_time,
                        ValidatorRequest.asset,
                        ValidatorRequest.time_length,
                        ValidatorRequest.time_increment,
                        ValidatorRequest.num_simulations,
                    )
                    .where(
                        and_(
                            # Compare start_time plus an interval (in seconds) to the scored_time.
                            window_start < scored_time,
                            # Compare start_time plus an interval (in seconds) to the window_days.
                            # This is to ensure that we only get requests that are within the window_days.
                            # Because we want to include in the moving average only the requests that are within the window_days.
                            window_start
                            >= scored_time - timedelta(days=window_days),
                            # Exclude records that have a matching miner_prediction via the NOT EXISTS clause.
                            not_(exists(subq)),
                            # Skip thinned-empty requests.
                            exists(alive_subq),
                            ValidatorRequest.time_length == time_length,
                            ValidatorRequest.asset.in_(asset_list),
                        )
                    )
                    .order_by(ValidatorRequest.start_time.asc())
                )

                results: list[ValidatorRequest] = []
                for row in connection.execute(query).fetchall():
                    vr = ValidatorRequest()
                    vr.id = row.id
                    vr.start_time = row.start_time
                    vr.asset = row.asset
                    vr.time_length = row.time_length
                    vr.time_increment = row.time_increment
                    # Bigtable hydration needs this to reshape the float32
                    # blob; without it `_hydrate_from_bigtable` crashes on
                    # `int(None)`.
                    vr.num_simulations = row.num_simulations
                    results.append(vr)

                return results
        except Exception as e:
            bt.logging.exception(
                f"in get_latest_prediction_request (got an exception): {e}"
            )
            return None

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=7),
        reraise=True,
        before=before_log(bt.logging._logger, logging.DEBUG),
    )
    def insert_new_miners(self, metagraph_info: list):
        """Insert or update miners table with the provided data."""
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    insert_stmt = (
                        insert(Miner)
                        .values(
                            [
                                {
                                    "miner_uid": miner["neuron_uid"],
                                    "coldkey": miner["coldkey"],
                                    "hotkey": miner["hotkey"],
                                }
                                for miner in metagraph_info
                            ]
                        )
                        .on_conflict_do_update(
                            # index_elements=["miner_uid", "coldkey", "hotkey"],
                            constraint="uq_miners_miner_uid_coldkey_hotkey",
                            # update the updated_at column
                            set_={"updated_at": datetime.now()},
                        )
                    )
                    connection.execute(insert_stmt)
        except Exception as e:
            bt.logging.exception(
                f"in insert_new_miners (got an exception): {e}"
            )

    def update_metagraph_history(self, metagraph_info: list):
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    insert_stmt = insert(MetagraphHistory).values(
                        metagraph_info
                    )
                    connection.execute(insert_stmt)
        except Exception as e:
            bt.logging.exception(
                f"in update_metagraph_history (got an exception): {e}"
            )

    @print_execution_time
    def get_miner_scores(
        self,
        scored_time: datetime,
        window_days: int,
        time_length: int,
        asset_list: list[str],
    ):
        min_scored_time = scored_time - timedelta(days=window_days)

        try:
            with self.engine.connect() as connection:
                query = text("""
                    SELECT
                        mp.miner_id,
                        ms.prompt_score_v3,
                        ms.scored_time,
                        vr.asset,
                        first_value((ms.score_details_v3->>'percentile90')::float)
                            OVER (PARTITION BY ms.scored_time ORDER BY ms.id) AS percentile90,
                        first_value((ms.score_details_v3->>'lowest_score')::float)
                            OVER (PARTITION BY ms.scored_time ORDER BY ms.id) AS lowest_score
                    FROM miner_scores ms
                    JOIN miner_predictions mp ON mp.id = ms.miner_predictions_id
                    JOIN validator_requests vr ON vr.id = mp.validator_requests_id
                    WHERE ms.scored_time > :min_scored_time
                      AND vr.time_length = :time_length
                      AND vr.asset = ANY(:asset_list)
                """)

                result = connection.execute(
                    query,
                    {
                        "min_scored_time": min_scored_time,
                        "time_length": time_length,
                        "asset_list": asset_list,
                    },
                )

                return pd.DataFrame(
                    result.fetchall(), columns=list(result.keys())
                )
        except Exception as e:
            bt.logging.exception(
                f"in get_miner_scores (got an exception): {e}"
            )
            return pd.DataFrame()

    def populate_miner_uid_in_miner_data(self, miner_data: list[dict]):
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    miner_uid_map = self.get_miner_ids_map(connection)
        except Exception as e:
            bt.logging.exception(
                f"in populate_miner_uid_in_miner_data (got an exception): {e}"
            )
            return None

        for row in miner_data:
            miner_id = row["miner_id"]
            row["miner_uid"] = (
                miner_uid_map[miner_id] if miner_id in miner_uid_map else None
            )

        return miner_data

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=7),
        reraise=True,
        before=before_log(bt.logging._logger, logging.DEBUG),
    )
    @print_execution_time
    def update_miner_rewards(self, miner_rewards_data: list[dict]):
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    insert_stmt = insert(MinerReward).values(
                        miner_rewards_data
                    )
                    connection.execute(insert_stmt)
        except Exception as e:
            bt.logging.exception(
                f"in update_miner_rewards (got an exception): {e}"
            )

    def update_weights_history(
        self,
        miner_uids: list[int],
        miner_weights: list[float],
        norm_miner_uids: list[str],
        norm_miner_weights: list[str],
        update_result: str,
        scored_time: datetime,
    ):
        update_weights_rows = {
            "miner_uids": miner_uids,
            "miner_weights": miner_weights,
            "norm_miner_uids": norm_miner_uids,
            "norm_miner_weights": norm_miner_weights,
            "update_result": update_result,
            "updated_at": scored_time.isoformat(),
        }

        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    insert_stmt = insert(WeightsUpdateHistory).values(
                        update_weights_rows
                    )
                    connection.execute(insert_stmt)
        except Exception as e:
            bt.logging.exception(
                f"in update_weights_history (got an exception): {e}"
            )

    @print_execution_time
    def density_tapering_predictions(
        self, prompt_config: prompt_config.PromptConfig
    ):
        """Density tapering at the validator_request grain.

        Short-term: keep many validator_requests per asset (high-density
        feed for downstream low-latency consumers, e.g. trading).
        Mid-term: keep only one validator_request per (asset, bucket) for
        scoring — 1 per hour LOW, 1 per 10 min HIGH. Scoring is a whole-
        request operation that happens after `time_length` has elapsed and
        the realized path is available, so partial / per-prediction scoring
        does not exist; either a request is the bucket keeper (and all its
        miners' predictions are kept) or it is redundant (and all its
        miners' predictions are soft-deleted together).

        Selects validator_requests with this cycle's `time_length` whose
        `start_time` is older than `prompt_config.thin_after_minutes`,
        buckets them by (asset, floor(epoch(start_time)/thin_bucket_seconds)),
        keeps one row per bucket, and soft-deletes every miner_prediction
        under the non-keeper requests by setting `deleted_at` and replacing
        `prediction` with a tombstone (same pattern as `cleanup_old_history`).

        The keeper is the row with the smallest `md5(id || thinning_salt)`,
        not the smallest id, so the kept request is spread across the bucket
        rather than always being its earliest member. The hash is stable per
        row (idempotent across runs): the global-min-hash row, once eligible,
        is always rn=1 and never thinned, so every bucket keeps exactly one
        row and converges to that keeper well before it enters scoring range.

        The most recent request per asset is additionally preserved (until
        it is older than `time_length`, i.e. enters scoring range) so
        low-latency downstream consumers always have the latest predictions
        to read.
        """
        now = datetime.now()
        thin_cutoff = now - timedelta(minutes=prompt_config.thin_after_minutes)
        # Protect the most recent request per asset so low-latency downstream
        # consumers always see the latest predictions. Only while it is still
        # newer than time_length (i.e. not yet eligible for scoring), so a
        # stale "latest" left by an issuance gap can't become a second
        # scorable row in its bucket.
        scoring_cutoff = now - timedelta(seconds=prompt_config.time_length)
        thin_sql = text("""
            WITH old AS (
                SELECT vr.id AS vr_id,
                       vr.asset,
                       (floor(
                           extract(epoch FROM vr.start_time)
                           / :bucket_seconds
                       )::bigint) AS bucket
                  FROM validator_requests vr
                 WHERE vr.time_length = :time_length
                   AND vr.start_time < :thin_cutoff
            ),
            ranked AS (
                SELECT vr_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY asset, bucket
                           ORDER BY md5(vr_id::text || :salt), vr_id ASC
                       ) AS rn
                  FROM old
            ),
            -- Freshest request per asset (the scoring bucket-keeper is
            -- still preserved independently via rn = 1). Gated to start_time
            -- newer than time_length so it is dropped once it enters
            -- scoring range, preventing a second scorable row in its bucket.
            latest_per_asset AS (
                SELECT DISTINCT ON (asset) id AS vr_id
                  FROM validator_requests
                 WHERE time_length = :time_length
                   AND start_time > :scoring_cutoff
                 ORDER BY asset, start_time DESC
            )
            UPDATE miner_predictions
               SET deleted_at = :now,
                   prediction = '{"deleted": true, "reason": "thinned"}'
                                ::jsonb
             WHERE deleted_at IS NULL
               AND validator_requests_id IN (
                   SELECT vr_id FROM ranked WHERE rn > 1
               )
               AND validator_requests_id NOT IN (
                   SELECT vr_id FROM latest_per_asset
               )
            """)
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    connection.execute(
                        thin_sql,
                        {
                            "bucket_seconds": (
                                prompt_config.thin_bucket_seconds
                            ),
                            "time_length": prompt_config.time_length,
                            "thin_cutoff": thin_cutoff,
                            "scoring_cutoff": scoring_cutoff,
                            "now": now,
                            "salt": self.thinning_salt,
                        },
                    )
        except Exception as e:
            bt.logging.exception(
                f"in prune_redundant_predictions (got an exception): {e}"
            )

    @print_execution_time
    def cleanup_old_history(self, prompt_config: prompt_config.PromptConfig):
        """Cleanup old history from the database."""
        cutoff_date = datetime.now() - timedelta(
            days=prompt_config.data_retention_days
        )
        cutoff_date_double = datetime.now() - timedelta(
            days=prompt_config.data_retention_days * 2
        )

        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    erase_predictions_statement = (
                        update(MinerPrediction)
                        .where(
                            MinerPrediction.created_at < cutoff_date,
                            MinerPrediction.deleted_at.is_(None),
                            MinerPrediction.validator_requests_id
                            == ValidatorRequest.id,
                            ValidatorRequest.time_length
                            == prompt_config.time_length,
                        )
                        .values(
                            deleted_at=datetime.now(),
                            prediction={
                                "deleted": True,
                                "reason": "light mode",
                            },
                        )
                    )
                    connection.execute(erase_predictions_statement)

                    delete_scores_statement = delete(MinerScore).where(
                        MinerScore.scored_time < cutoff_date_double,
                        MinerScore.miner_predictions_id == MinerPrediction.id,
                        MinerPrediction.validator_requests_id
                        == ValidatorRequest.id,
                        ValidatorRequest.time_length
                        == prompt_config.time_length,
                    )
                    connection.execute(delete_scores_statement)

                    erase_validator_requests_statement = (
                        update(ValidatorRequest)
                        .where(
                            ValidatorRequest.start_time < cutoff_date_double,
                            ValidatorRequest.time_length
                            == prompt_config.time_length,
                        )
                        .values(real_prices=[])
                    )
                    connection.execute(erase_validator_requests_statement)

        except Exception as e:
            bt.logging.exception(
                f"in cleanup_old_history (got an exception): {e}"
            )

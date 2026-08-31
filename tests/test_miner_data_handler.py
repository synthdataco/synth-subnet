from datetime import datetime, timedelta
import hashlib
import os

import pytest
from sqlalchemy import Engine, select, delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from synth.db.models import (
    MinerPrediction,
    MinerScore,
    ValidatorRequest,
    Miner,
)
from synth.validator import response_validation_v2
from synth.validator import competition_config
from synth.validator.prompt_config import PromptConfig
from synth.simulation_input import SimulationInput
from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator.reward import get_rewards_multiprocess
from tests.utils import (
    generate_values,
    prepare_random_predictions,
    recent_start_time,
)


@pytest.fixture(scope="function", autouse=True)
def setup_data(db_engine: Engine):
    with db_engine.connect() as connection:
        with connection.begin():
            connection.execute(delete(MinerPrediction))
            connection.execute(delete(ValidatorRequest))


def test_get_values_within_range(db_engine: Engine):
    """
    Test retrieving values within the valid time range.
    2024-11-20T00:00:00       2024-11-20T23:55:00
             |-------------------------|                       (Prediction range)

                                            2024-11-22T00:00:00
                                                    |-|        (Scored Time)
    """
    miner_uids = [10]
    with db_engine.connect() as connection:
        with connection.begin():
            insert_stmt_validator = insert(Miner).values(
                [{"miner_uid": uid} for uid in miner_uids]
            )
            connection.execute(insert_stmt_validator)

    miner_uid = miner_uids[0]
    start_time = "2024-11-20T00:00:00"
    scored_time = datetime.fromisoformat("2024-11-22T00:00:00")
    simulation_input = SimulationInput(
        asset="BTC",
        start_time=start_time,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    values = generate_values(datetime.fromisoformat(start_time))
    simulation_data = {
        miner_uid: (values, response_validation_v2.CORRECT, "12")
    }
    handler = MinerDataHandler(db_engine)
    handler.save_responses(simulation_data, simulation_input, datetime.now())

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["BTC"]
    )
    assert validator_requests is not None
    assert len(validator_requests) == 1

    result = handler.get_predictions_by_request(validator_requests[0])

    # get only second element from the result tuple
    # that corresponds to the prediction result
    assert result is not None
    pred = result[0]
    prediction = pred.prediction

    assert len(prediction) == 1
    assert len(prediction[0]) == 288
    assert prediction[0][0] == {"time": "2024-11-20T00:00:00", "price": 90000}
    assert prediction[0][287] == {
        "time": "2024-11-20T23:55:00",
        "price": 233500,
    }


def test_get_values_ongoing_range(db_engine: Engine):
    """
    Test retrieving values when current_time overlaps with the range.
    2024-11-20T00:00:00       2024-11-20T23:55:00
             |-------------------------|         (Prediction range)

                2024-11-20T12:00:00
                        |-|                      (Scored Time)
    """
    miner_uids = [10]
    with db_engine.connect() as connection:
        with connection.begin():
            insert_stmt_validator = insert(Miner).values(
                [{"miner_uid": uid} for uid in miner_uids]
            )
            connection.execute(insert_stmt_validator)

    miner_uid = miner_uids[0]
    start_time = "2024-11-20T00:00:00"
    scored_time = datetime.fromisoformat("2024-11-20T12:00:00")

    simulation_input = SimulationInput(
        asset="BTC",
        start_time=start_time,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    values = generate_values(datetime.fromisoformat(start_time))
    simulation_data = {
        miner_uid: (values, response_validation_v2.CORRECT, "12")
    }
    handler = MinerDataHandler(db_engine)
    handler.save_responses(simulation_data, simulation_input, datetime.now())

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["BTC"]
    )

    assert validator_requests is not None
    assert len(validator_requests) == 0


def test_multiple_records_for_same_miner(db_engine: Engine):
    """
    Test handling multiple records for the same miner.
    Should take "Prediction range 2" as the latest one

    2024-11-20T00:00:00       2024-11-20T23:55:00
             |-------------------------|                             (Prediction range 1)

                  2024-11-20T12:00:00       2024-11-21T11:55:00
                           |-------------------------|               (Prediction range 2)

                                                  2024-11-21T15:00:00
                                                          |-|        (Current Time)
    """
    miner_uids = [10]
    with db_engine.connect() as connection:
        with connection.begin():
            insert_stmt_validator = insert(Miner).values(
                [{"miner_uid": uid} for uid in miner_uids]
            )
            connection.execute(insert_stmt_validator)

    miner_uid = miner_uids[0]
    start_time_1 = "2024-11-20T00:00:00+00:00"
    start_time_2 = "2024-11-20T12:00:00+00:00"
    scored_time = datetime.fromisoformat("2024-11-21T15:00:00+00:00")

    simulation_input_1 = SimulationInput(
        asset="BTC",
        start_time=start_time_1,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    simulation_input_2 = SimulationInput(
        asset="BTC",
        start_time=start_time_2,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    handler = MinerDataHandler(db_engine)

    values_1 = generate_values(datetime.fromisoformat(start_time_1))
    simulation_data_1 = {
        miner_uid: (values_1, response_validation_v2.CORRECT, "12")
    }
    handler.save_responses(
        simulation_data_1, simulation_input_1, datetime.now()
    )

    values_2 = generate_values(datetime.fromisoformat(start_time_2))
    simulation_data_2 = {
        miner_uid: (values_2, response_validation_v2.CORRECT, "12")
    }
    handler.save_responses(
        simulation_data_2, simulation_input_2, datetime.now()
    )

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["BTC"]
    )
    assert validator_requests is not None
    assert len(validator_requests) == 2

    result = handler.get_predictions_by_request(validator_requests[1])

    assert result is not None
    pred = result[0]
    prediction = pred.prediction

    assert len(prediction) == 1
    assert len(prediction[0]) == 288
    assert prediction[0][0] == {
        "time": "2024-11-20T12:00:00+00:00",
        "price": 90000,
    }
    assert prediction[0][287] == {
        "time": "2024-11-21T11:55:00+00:00",
        "price": 233500,
    }


def test_multiple_records_for_same_miner_with_overlapping(db_engine: Engine):
    """
    Test handling multiple records for the same miner with overlapping records.
    Should take "Prediction range 1" as the latest one

    2024-11-20T00:00:00       2024-11-20T23:55:00
             |-------------------------|                             (Prediction range 1)

                  2024-11-20T12:00:00       2024-11-21T11:55:00
                           |-------------------------|               (Prediction range 2)

                                    2024-11-21T03:00:00
                                            |-|                      (Scored Time)
    """
    miner_uids = [10]
    with db_engine.connect() as connection:
        with connection.begin():
            insert_stmt_validator = insert(Miner).values(
                [{"miner_uid": uid} for uid in miner_uids]
            )
            connection.execute(insert_stmt_validator)

    miner_uid = miner_uids[0]
    start_time_1 = "2024-11-20T00:00:00+00:00"
    start_time_2 = "2024-11-20T12:00:00+00:00"
    scored_time = datetime.fromisoformat("2024-11-21T03:00:00+00:00")

    simulation_input_1 = SimulationInput(
        asset="BTC",
        start_time=start_time_1,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    simulation_input_2 = SimulationInput(
        asset="BTC",
        start_time=start_time_2,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    handler = MinerDataHandler(db_engine)

    values_1 = generate_values(datetime.fromisoformat(start_time_1))
    simulation_data_1 = {
        miner_uid: (values_1, response_validation_v2.CORRECT, "12")
    }
    handler.save_responses(
        simulation_data_1, simulation_input_1, datetime.now()
    )

    values_2 = generate_values(datetime.fromisoformat(start_time_2))
    simulation_data_2 = {
        miner_uid: (values_2, response_validation_v2.CORRECT, "12")
    }
    handler.save_responses(
        simulation_data_2, simulation_input_2, datetime.now()
    )

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["BTC"]
    )
    assert validator_requests is not None
    assert len(validator_requests) == 1

    result = handler.get_predictions_by_request(validator_requests[0])

    # get only second element from the result tuple
    # that corresponds to the prediction result
    assert result is not None
    pred = result[0]
    prediction = pred.prediction

    assert len(prediction) == 1
    assert len(prediction[0]) == 288
    assert prediction[0][0] == {
        "time": "2024-11-20T00:00:00+00:00",
        "price": 90000,
    }
    assert prediction[0][287] == {
        "time": "2024-11-20T23:55:00+00:00",
        "price": 233500,
    }


def test_no_data_for_miner(db_engine: Engine):
    """Test retrieving values for a miner that doesn't exist."""
    scored_time = datetime.fromisoformat("2024-11-20T12:00:00+00:00")

    handler = MinerDataHandler(db_engine)

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["BTC"]
    )
    assert validator_requests is not None
    assert len(validator_requests) == 0


def test_get_values_incorrect_format(db_engine: Engine):
    """
    Test retrieving values within the valid time range.
    2024-11-20T00:00:00       2024-11-20T23:55:00
             |-------------------------|                       (Prediction range)

                                            2024-11-22T00:00:00
                                                    |-|        (Scored Time)
    """
    miner_uids = [10]
    with db_engine.connect() as connection:
        with connection.begin():
            insert_stmt_validator = insert(Miner).values(
                [{"miner_uid": uid} for uid in miner_uids]
            )
            connection.execute(insert_stmt_validator)

    miner_uid = miner_uids[0]
    start_time = "2024-11-20T00:00:00"
    scored_time = datetime.fromisoformat("2024-11-22T00:00:00")
    simulation_input = SimulationInput(
        asset="BTC",
        start_time=start_time,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    error_string = "some errors in the format"
    simulation_data: dict = {miner_uid: ([], error_string, "12")}
    handler = MinerDataHandler(db_engine)
    handler.save_responses(simulation_data, simulation_input, datetime.now())

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["BTC"]
    )
    assert validator_requests is not None
    assert len(validator_requests) == 1
    result = handler.get_predictions_by_request(validator_requests[0])

    assert result is not None
    pred = result[0]
    prediction = pred.prediction
    format_validation = pred.format_validation

    assert len(prediction) == 0
    assert format_validation == error_string


def test_set_get_scores(db_engine: Engine):
    handler = MinerDataHandler(db_engine)
    price_data_provider = PriceDataProvider()
    start_time = recent_start_time()
    scored_time = datetime.fromisoformat(start_time) + timedelta(
        hours=24, minutes=5
    )
    handler, _, _ = prepare_random_predictions(db_engine, start_time)

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["HYPE"]
    )
    assert validator_requests is not None
    assert len(validator_requests) == 1

    prompt_scores, detailed_info, real_prices = get_rewards_multiprocess(
        handler,
        price_data_provider,
        validator_requests[0],
        competition_config.CRYPTO_24H,
    )

    assert prompt_scores is not None

    handler.set_miner_scores(
        real_prices, int(validator_requests[0].id), detailed_info, scored_time
    )

    miner_scores_df = handler.get_miner_scores(
        scored_time=scored_time,
        window_days=4,
        time_length=86400,
        asset_list=["HYPE"],
    )

    assert not miner_scores_df.empty
    assert miner_scores_df["percentile95"].notna().all()
    expected_percentile95 = detailed_info[0]["percentile95"]
    assert (miner_scores_df["percentile95"] == expected_percentile95).all()

    # Transition regression: rows written before the p95-on-miss change
    # carry the old 'percentile90' JSON key; the COALESCE fallback in
    # get_miner_scores must still surface them as percentile95.
    with db_engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text("""
                    UPDATE miner_scores
                    SET score_details_v3 = (score_details_v3 - 'percentile95')
                        || jsonb_build_object(
                            'percentile90',
                            score_details_v3->'percentile95')
                    WHERE scored_time = :scored_time
                    """),
                {"scored_time": scored_time},
            )

    old_key_df = handler.get_miner_scores(
        scored_time=scored_time,
        window_days=4,
        time_length=86400,
        asset_list=["HYPE"],
    )

    assert not old_key_df.empty
    assert (old_key_df["percentile95"] == expected_percentile95).all()


def test_insert_new_miners(db_engine: Engine):
    handler = MinerDataHandler(db_engine)

    with db_engine.connect() as connection:
        with connection.begin():
            initial_len = len(connection.execute(select(Miner)).fetchall())

    handler.insert_new_miners(
        [{"neuron_uid": 111, "coldkey": "coldkey111", "hotkey": "hotkey111"}]
    )

    with db_engine.connect() as connection:
        with connection.begin():
            assert (
                len(connection.execute(select(Miner)).fetchall())
                == initial_len + 1
            )

    handler.insert_new_miners(
        [{"neuron_uid": 111, "coldkey": "coldkey111", "hotkey": "hotkey111"}]
    )

    # Should not insert the same miner again
    with db_engine.connect() as connection:
        with connection.begin():
            assert (
                len(connection.execute(select(Miner)).fetchall())
                == initial_len + 1
            )

    handler.insert_new_miners(
        [
            {
                "neuron_uid": 111,
                "coldkey": "coldkey111-changed",
                "hotkey": "hotkey111-changed",
            }
        ]
    )

    # Should insert new miner with updated values
    with db_engine.connect() as connection:
        with connection.begin():
            assert (
                len(connection.execute(select(Miner)).fetchall())
                == initial_len + 2
            )


def test_set_miner_scores_upsert_preserves_individual_values(
    db_engine: Engine,
):
    """Test that on_conflict_do_update uses each row's own values, not the last row's.

    Previously, the on_conflict_do_update clause referenced a stale loop variable
    `row` which always pointed to the LAST element of reward_details. This meant
    on upsert conflicts, all rows got the last miner's scores.

    Example of the bug:
        reward_details = [
            {"miner_uid": 1, "prompt_score_v3": 0.95, ...},  # miner 1
            {"miner_uid": 2, "prompt_score_v3": 0.30, ...},  # miner 2
        ]
        # After loop: row = miner 2's dict
        # On conflict for miner 1 -> got miner 2's score (0.30) instead of 0.95
    """
    handler = MinerDataHandler(db_engine)
    start_time = "2024-11-20T00:00:00+00:00"
    scored_time = datetime.fromisoformat("2024-11-22T00:00:00+00:00")

    # Setup: create miners and save predictions to get valid miner_predictions_ids
    handler, simulation_input, miner_uids = prepare_random_predictions(
        db_engine, start_time
    )

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["HYPE"]
    )
    assert len(validator_requests) >= 1

    # Get prediction IDs for the miners
    with db_engine.connect() as connection:
        predictions = connection.execute(
            select(MinerPrediction.id, MinerPrediction.miner_id).where(
                MinerPrediction.validator_requests_id
                == validator_requests[0].id
            )
        ).fetchall()

    assert len(predictions) >= 2, "Need at least 2 predictions to test upsert"

    # Create reward_details with DIFFERENT scores for each miner
    reward_details = []
    scores = [0.95, 0.30, 0.10, 0.50]  # deliberately different
    for i, pred in enumerate(predictions):
        reward_details.append(
            {
                "miner_uid": i,
                "miner_prediction_id": pred.id,
                "total_crps": scores[i % len(scores)],
                "percentile95": 1.0,
                "lowest_score": 0.01,
                "prompt_score_v3": scores[i % len(scores)],
                "crps_data": [{"crps": scores[i % len(scores)]}],
            }
        )

    # First insert
    handler.set_miner_scores(
        [], int(validator_requests[0].id), reward_details, scored_time
    )

    # Second insert (same prediction IDs -> triggers upsert conflict)
    # Use different scores to verify each row gets its OWN updated values
    updated_reward_details = []
    updated_scores = [0.11, 0.22, 0.33, 0.44]
    for i, pred in enumerate(predictions):
        updated_reward_details.append(
            {
                "miner_uid": i,
                "miner_prediction_id": pred.id,
                "total_crps": updated_scores[i % len(updated_scores)],
                "percentile95": 2.0,
                "lowest_score": 0.02,
                "prompt_score_v3": updated_scores[i % len(updated_scores)],
                "crps_data": [
                    {"crps": updated_scores[i % len(updated_scores)]}
                ],
            }
        )

    handler.set_miner_scores(
        [],
        int(validator_requests[0].id),
        updated_reward_details,
        scored_time,
    )

    # Verify: each miner should have their OWN updated score, not the last
    # miner's
    with db_engine.connect() as connection:
        results = connection.execute(
            select(
                MinerScore.miner_predictions_id,
                MinerScore.prompt_score_v3,
                MinerScore.score_details_v3,
            )
            .where(
                MinerScore.miner_predictions_id.in_(
                    [p.id for p in predictions]
                )
            )
            .order_by(MinerScore.miner_predictions_id)
        ).fetchall()

    assert len(results) == len(predictions)

    for i, result in enumerate(results):
        expected_score = updated_scores[i % len(updated_scores)]
        assert result.prompt_score_v3 == pytest.approx(expected_score), (
            f"Miner {i}: expected prompt_score_v3={expected_score}, "
            f"got {result.prompt_score_v3}. "
            f"Bug: all miners got last miner's score instead of their own."
        )


# ----- prune_redundant_predictions -----------------------------------------

LOW_TEST_CONFIG = PromptConfig(
    asset_list=["BTC"],
    label="low",
    time_length=86400,
    time_increment=300,
    cycle_interval_minutes=5,
    timeout_extra_seconds=60,
    thin_after_minutes=30,
    thin_bucket_seconds=3600,
)

HIGH_TEST_CONFIG = PromptConfig(
    asset_list=["BTC"],
    label="high",
    time_length=3600,
    time_increment=60,
    cycle_interval_minutes=2,
    timeout_extra_seconds=60,
    thin_after_minutes=10,
    thin_bucket_seconds=600,
)


def _insert_miner(connection, miner_uid: int) -> int:
    existing = connection.execute(
        select(Miner.id).where(Miner.miner_uid == miner_uid)
    ).fetchone()
    if existing is not None:
        return int(existing.id)
    result = connection.execute(
        insert(Miner).values(miner_uid=miner_uid).returning(Miner.id)
    )
    return int(result.fetchone().id)


def _insert_prediction(
    connection,
    miner_id: int,
    asset: str,
    time_length: int,
    start_time: datetime,
    created_at: datetime,
    payload: list,
    bigtable_key: str | None = None,
) -> int:
    vr_id = (
        connection.execute(
            insert(ValidatorRequest)
            .values(
                start_time=start_time,
                asset=asset,
                time_increment=300,
                time_length=time_length,
                num_simulations=1,
                request_time=created_at,
            )
            .returning(ValidatorRequest.id)
        )
        .fetchone()
        .id
    )
    mp_id = (
        connection.execute(
            insert(MinerPrediction)
            .values(
                validator_requests_id=vr_id,
                miner_uid=0,
                miner_id=miner_id,
                prediction=payload,
                format_validation=response_validation_v2.CORRECT,
                process_time=1.0,
                created_at=created_at,
                bigtable_key=bigtable_key,
            )
            .returning(MinerPrediction.id)
        )
        .fetchone()
        .id
    )
    return int(mp_id)


def _fetch_prediction_state(connection, mp_id: int):
    return connection.execute(
        select(
            MinerPrediction.id,
            MinerPrediction.prediction,
            MinerPrediction.deleted_at,
        ).where(MinerPrediction.id == mp_id)
    ).fetchone()


def _split_alive_thinned(connection, mp_ids: list[int]):
    """Partition the given prediction ids into (alive, thinned) after a
    tapering run. Keeper selection is a salted hash of the request id, so
    tests assert the survivor *count* per bucket, not which id survives.
    Every thinned row must carry the tombstone."""
    alive, thinned = [], []
    for mp_id in mp_ids:
        row = _fetch_prediction_state(connection, mp_id)
        assert row is not None
        if row.deleted_at is None:
            assert isinstance(row.prediction, list)
            alive.append(mp_id)
        else:
            assert row.prediction == {"deleted": True, "reason": "thinned"}
            thinned.append(mp_id)
    return alive, thinned


def test_prune_leaves_recent_requests_untouched(db_engine: Engine):
    """Validator_requests newer than `thin_after_minutes` are not pruned —
    even when many in the same bucket. Short-term density survives for the
    downstream low-latency consumer."""
    now = datetime.now()
    with db_engine.connect() as connection:
        with connection.begin():
            miner_a = _insert_miner(connection, miner_uid=300)
            miner_b = _insert_miner(connection, miner_uid=301)
            ids: list[int] = []
            for m in (2, 5, 10):
                start = now - timedelta(minutes=m)
                for miner_id in (miner_a, miner_b):
                    ids.append(
                        _insert_prediction(
                            connection,
                            miner_id=miner_id,
                            asset="BTC",
                            time_length=LOW_TEST_CONFIG.time_length,
                            start_time=start,
                            created_at=start,
                            payload=[[{"price": float(m)}]],
                        )
                    )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        for mp_id in ids:
            row = _fetch_prediction_state(connection, mp_id)
            assert row is not None
            assert row.deleted_at is None
            assert isinstance(row.prediction, list)


def test_prune_collapses_old_requests_in_same_bucket(db_engine: Engine):
    """Several old validator_requests for the same asset land in the same
    hour bucket → exactly one request is the keeper (salted-hash pick) and
    every prediction on the other requests gets the `thinned` tombstone."""
    bucket_anchor = datetime(2026, 1, 1, 12, 0, 0)
    ids: list[int] = []
    with db_engine.connect() as connection:
        with connection.begin():
            miner_a = _insert_miner(connection, miner_uid=310)
            miner_b = _insert_miner(connection, miner_uid=311)

            for minute in (5, 25, 50):
                start = bucket_anchor + timedelta(minutes=minute)
                for miner_id in (miner_a, miner_b):
                    ids.append(
                        _insert_prediction(
                            connection,
                            miner_id=miner_id,
                            asset="SOL",
                            time_length=LOW_TEST_CONFIG.time_length,
                            start_time=start,
                            created_at=start,
                            payload=[[{"price": float(minute)}]],
                        )
                    )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        alive, thinned = _split_alive_thinned(connection, ids)
        # Each _insert_prediction creates its own validator_request, so the
        # single surviving request contributes exactly one alive prediction.
        assert len(alive) == 1
        assert len(thinned) == len(ids) - 1


def test_prune_keeps_one_request_per_asset_per_bucket(db_engine: Engine):
    """Different assets in the same bucket are independent — each asset
    keeps exactly one request. Different buckets are independent —
    each bucket keeps its own request."""
    bucket_anchor = datetime(2026, 1, 1, 12, 0, 0)
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=320)
            # bucket 1, BTC: two requests → keep first, prune second
            btc_keep = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="BTC",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(minutes=5),
                created_at=bucket_anchor + timedelta(minutes=5),
                payload=[[{"price": 1.0}]],
            )
            btc_prune = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="BTC",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(minutes=25),
                created_at=bucket_anchor + timedelta(minutes=25),
                payload=[[{"price": 2.0}]],
            )
            # bucket 1, ETH: one request → keep
            eth_keep = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(minutes=10),
                created_at=bucket_anchor + timedelta(minutes=10),
                payload=[[{"price": 3.0}]],
            )
            # bucket 2, BTC: one request → keep
            btc_next_bucket = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="BTC",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(hours=1, minutes=5),
                created_at=bucket_anchor + timedelta(hours=1, minutes=5),
                payload=[[{"price": 4.0}]],
            )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        # BTC bucket 1 has two requests → exactly one survives.
        btc_alive, _ = _split_alive_thinned(connection, [btc_keep, btc_prune])
        assert len(btc_alive) == 1
        # Single-request buckets are always kept.
        assert _fetch_prediction_state(connection, eth_keep).deleted_at is None
        assert (
            _fetch_prediction_state(connection, btc_next_bucket).deleted_at
            is None
        )


def test_prune_scopes_by_time_length(db_engine: Engine):
    """LOW pruning must not touch HIGH validator_requests, and vice versa."""
    bucket_anchor = datetime(2026, 1, 1, 12, 0, 0)
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=330)
            low_kept = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=bucket_anchor,
                created_at=bucket_anchor,
                payload=[[{"price": 1.0}]],
            )
            low_extra = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(minutes=30),
                created_at=bucket_anchor + timedelta(minutes=30),
                payload=[[{"price": 2.0}]],
            )
            high_a = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=HIGH_TEST_CONFIG.time_length,
                start_time=bucket_anchor,
                created_at=bucket_anchor,
                payload=[[{"price": 9.0}]],
            )
            high_b = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=HIGH_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(minutes=4),
                created_at=bucket_anchor + timedelta(minutes=4),
                payload=[[{"price": 8.0}]],
            )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        # LOW bucket has two requests → exactly one survives.
        low_alive, _ = _split_alive_thinned(connection, [low_kept, low_extra])
        assert len(low_alive) == 1
        # HIGH untouched.
        assert _fetch_prediction_state(connection, high_a).deleted_at is None
        assert _fetch_prediction_state(connection, high_b).deleted_at is None


def test_prune_high_bucket_is_ten_minutes(db_engine: Engine):
    """HIGH bucket = 600 s. Two requests inside the same 10-min bucket
    collapse to one; an adjacent-bucket request survives."""
    bucket_anchor = datetime(2026, 1, 1, 11, 0, 0)
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=340)
            kept_a = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="HYPE",
                time_length=HIGH_TEST_CONFIG.time_length,
                start_time=bucket_anchor,
                created_at=bucket_anchor,
                payload=[[{"price": 1.0}]],
            )
            extra_a = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="HYPE",
                time_length=HIGH_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(minutes=4),
                created_at=bucket_anchor + timedelta(minutes=4),
                payload=[[{"price": 2.0}]],
            )
            kept_b = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="HYPE",
                time_length=HIGH_TEST_CONFIG.time_length,
                start_time=bucket_anchor + timedelta(minutes=12),
                created_at=bucket_anchor + timedelta(minutes=12),
                payload=[[{"price": 3.0}]],
            )

    MinerDataHandler(db_engine).density_tapering_predictions(HIGH_TEST_CONFIG)

    with db_engine.connect() as connection:
        # Two requests in the same 10-min bucket → exactly one survives.
        alive, _ = _split_alive_thinned(connection, [kept_a, extra_a])
        assert len(alive) == 1
        # Adjacent bucket is independent → kept.
        assert _fetch_prediction_state(connection, kept_b).deleted_at is None


def test_thinning_without_salt_falls_back_to_md5_id(
    db_engine: Engine, monkeypatch
):
    """THINNING_SALT is optional: unset, the handler still constructs and
    tapering collapses each bucket to exactly one keeper via md5(id)."""
    monkeypatch.delenv("THINNING_SALT", raising=False)
    bucket_anchor = datetime(2026, 1, 1, 7, 0, 0)
    ids: list[int] = []
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=370)
            for minute in (5, 25, 45):
                start = bucket_anchor + timedelta(minutes=minute)
                ids.append(
                    _insert_prediction(
                        connection,
                        miner_id=miner_id,
                        asset="SOL",
                        time_length=LOW_TEST_CONFIG.time_length,
                        start_time=start,
                        created_at=start,
                        payload=[[{"price": float(minute)}]],
                    )
                )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        vr_by_mp = {
            mp_id: connection.execute(
                select(MinerPrediction.validator_requests_id).where(
                    MinerPrediction.id == mp_id
                )
            ).scalar_one()
            for mp_id in ids
        }
        expected_keeper_mp = min(
            ids,
            key=lambda mp: hashlib.md5(str(vr_by_mp[mp]).encode()).hexdigest(),
        )
        alive, thinned = _split_alive_thinned(connection, ids)
    assert alive == [expected_keeper_mp]
    assert len(thinned) == len(ids) - 1


def test_tapering_keeper_is_stable_across_runs(db_engine: Engine):
    """Keeper selection is deterministic per salt, so re-running tapering
    keeps the same survivor (idempotent) instead of thinning the bucket
    down to zero over successive runs."""
    bucket_anchor = datetime(2026, 1, 1, 9, 0, 0)
    ids: list[int] = []
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=350)
            for minute in (5, 20, 35, 50):
                start = bucket_anchor + timedelta(minutes=minute)
                ids.append(
                    _insert_prediction(
                        connection,
                        miner_id=miner_id,
                        asset="XRP",
                        time_length=LOW_TEST_CONFIG.time_length,
                        start_time=start,
                        created_at=start,
                        payload=[[{"price": float(minute)}]],
                    )
                )

    handler = MinerDataHandler(db_engine)
    handler.density_tapering_predictions(LOW_TEST_CONFIG)
    with db_engine.connect() as connection:
        alive_first, _ = _split_alive_thinned(connection, ids)
    assert len(alive_first) == 1

    handler.density_tapering_predictions(LOW_TEST_CONFIG)
    with db_engine.connect() as connection:
        alive_second, _ = _split_alive_thinned(connection, ids)
    assert alive_second == alive_first


def test_tapering_keeper_matches_salted_hash(db_engine: Engine):
    """The surviving request is the one with the smallest md5(id || salt),
    proving the keeper is driven by the secret salt and not by id order."""
    salt = os.environ["THINNING_SALT"]
    bucket_anchor = datetime(2026, 1, 1, 8, 0, 0)
    mp_ids: list[int] = []
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=360)
            for minute in (2, 12, 22, 32, 42, 52):
                start = bucket_anchor + timedelta(minutes=minute)
                mp_ids.append(
                    _insert_prediction(
                        connection,
                        miner_id=miner_id,
                        asset="XAU",
                        time_length=LOW_TEST_CONFIG.time_length,
                        start_time=start,
                        created_at=start,
                        payload=[[{"price": float(minute)}]],
                    )
                )

    with db_engine.connect() as connection:
        vr_by_mp = {
            mp_id: connection.execute(
                select(MinerPrediction.validator_requests_id).where(
                    MinerPrediction.id == mp_id
                )
            ).scalar_one()
            for mp_id in mp_ids
        }
    expected_keeper_mp = min(
        mp_ids,
        key=lambda mp: hashlib.md5(
            f"{vr_by_mp[mp]}{salt}".encode()
        ).hexdigest(),
    )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        alive, _ = _split_alive_thinned(connection, mp_ids)
    assert alive == [expected_keeper_mp]


def test_scoring_path_skips_thinned_requests(db_engine: Engine):
    """Regression: density_tapering_predictions tombstones every prediction
    on redundant validator_requests ~30 min after start_time, but those
    requests only become scoring-eligible at start_time + time_length +
    SCORING_GATE_SECONDS (~24 h later for LOW). Without filtering, the
    scorer would later load the `{"deleted": true}` tombstones and feed
    them to CRPS as garbage.

    This test plants two requests in the same hour-bucket, runs the
    thinning, then verifies:
      - `get_validator_requests_to_score` returns only the keeper.
      - `get_predictions_by_request` on the thinned request returns nothing.
    """
    # 25 h ago, normalized to a stable minute so both rows share the same
    # floor(epoch/3600) bucket regardless of wall-clock minute.
    start = (datetime.now() - timedelta(hours=25)).replace(
        minute=10, second=0, microsecond=0
    )
    scored_time = start + timedelta(hours=25)
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=320)
            keeper_mp = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=start,
                created_at=start,
                payload=[[{"price": 1.0}]],
            )
            thinned_mp = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=start + timedelta(minutes=5),
                created_at=start + timedelta(minutes=5),
                payload=[[{"price": 2.0}]],
            )

    handler = MinerDataHandler(db_engine)
    handler.density_tapering_predictions(LOW_TEST_CONFIG)

    # The keeper is a salted-hash pick, so discover which of the two
    # same-bucket requests survived and assert the scorer sees only it.
    with db_engine.connect() as connection:
        alive, thinned = _split_alive_thinned(
            connection, [keeper_mp, thinned_mp]
        )
        assert len(alive) == 1
        assert len(thinned) == 1
        survivor_vr_id = connection.execute(
            select(MinerPrediction.validator_requests_id).where(
                MinerPrediction.id == alive[0]
            )
        ).scalar_one()
        thinned_vr_id = connection.execute(
            select(MinerPrediction.validator_requests_id).where(
                MinerPrediction.id == thinned[0]
            )
        ).scalar_one()

    requests = handler.get_validator_requests_to_score(
        scored_time=scored_time,
        window_days=10,
        time_length=LOW_TEST_CONFIG.time_length,
        asset_list=["ETH"],
    )
    returned_ids = {int(r.id) for r in (requests or [])}
    assert survivor_vr_id in returned_ids
    assert thinned_vr_id not in returned_ids

    # Defense in depth: even if a future caller bypasses the filter
    # above, the predictions query must not surface tombstones.
    with Session(db_engine) as session:
        thinned_vr = session.execute(
            select(ValidatorRequest).where(
                ValidatorRequest.id == thinned_vr_id
            )
        ).scalar_one()
    assert handler.get_predictions_by_request(thinned_vr) == []


# --- Bigtable backend integration -----------------------------------------


def _production_format_prediction(num_simulations: int, num_timesteps: int):
    """Build a prediction in production wire format (header + float paths)."""
    paths = [
        [float(s * 1000 + t) for t in range(num_timesteps)]
        for s in range(num_simulations)
    ]
    return [1700000000, 300, *paths]


def test_save_responses_with_bigtable_stores_sentinel_and_key(
    db_engine: Engine,
):
    """save_responses with bigtable backend stores sentinel JSON in
    `prediction` and the row key in `bigtable_key`; correctness is delegated
    to the Bigtable backend.
    """
    miner_uid = 10
    with db_engine.connect() as connection:
        with connection.begin():
            connection.execute(
                insert(Miner).values([{"miner_uid": miner_uid}])
            )

    start_time = "2026-05-25T12:00:00"
    simulation_input = SimulationInput(
        asset="BTC",
        start_time=start_time,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )
    prediction = _production_format_prediction(1, 289)
    good_data = {
        miner_uid: (prediction, response_validation_v2.CORRECT, "1.2"),
    }

    # The handler treats bigtable_key as opaque; any stable string works.
    expected_key = "bt-key-for-miner-10"

    class FakeBigtable:
        def __init__(self):
            self.write_calls = []

        def write_predictions(
            self,
            simulation_input,
            miner_predictions,
            miner_id_map,
        ):
            self.write_calls.append(simulation_input.time_length)
            return {miner_uid: expected_key}

        def read_predictions(self, validator_request, keys):
            # paths only, as the storage contract specifies
            return {expected_key: prediction[2:]}

    fake = FakeBigtable()
    handler = MinerDataHandler(db_engine, bigtable_storage=fake)
    handler.save_responses(
        good_data,
        simulation_input,
        datetime.now(),
    )

    assert fake.write_calls == [simulation_input.time_length]

    with db_engine.connect() as connection:
        row = connection.execute(
            select(
                MinerPrediction.prediction,
                MinerPrediction.bigtable_key,
            )
        ).one()
    assert row.prediction == {"stored": "bigtable"}
    assert row.bigtable_key == expected_key


def test_get_predictions_by_request_hydrates_from_bigtable(
    db_engine: Engine,
):
    miner_uid = 10
    with db_engine.connect() as connection:
        with connection.begin():
            connection.execute(
                insert(Miner).values([{"miner_uid": miner_uid}])
            )

    start_time = "2026-05-25T12:00:00"
    simulation_input = SimulationInput(
        asset="BTC",
        start_time=start_time,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )
    prediction = _production_format_prediction(1, 289)
    # The handler treats bigtable_key as opaque; any stable string works.
    expected_key = "bt-key-for-miner-10"

    class FakeBigtable:
        def write_predictions(self, **_):
            return {miner_uid: expected_key}

        def read_predictions(self, validator_request, keys):
            assert keys == [expected_key]
            assert (
                validator_request.time_length == simulation_input.time_length
            )
            # Bigtable hydration reshapes the float32 blob using
            # num_simulations; if get_validator_requests_to_score forgets
            # to populate it, that path crashes in production with int(None).
            assert (
                validator_request.num_simulations
                == simulation_input.num_simulations
            )
            return {expected_key: prediction[2:]}

    fake = FakeBigtable()
    handler = MinerDataHandler(db_engine, bigtable_storage=fake)
    handler.save_responses(
        {miner_uid: (prediction, response_validation_v2.CORRECT, "1.2")},
        simulation_input,
        datetime.now(),
    )

    validator_request = handler.get_validator_requests_to_score(
        datetime.fromisoformat(start_time) + timedelta(days=2),
        7,
        86400,
        ["BTC"],
    )[0]
    result = handler.get_predictions_by_request(validator_request)
    assert len(result) == 1
    pred = result[0]
    # Hydration derives the [start_ts, time_increment] header from the
    # validator_requests row (which Postgres stores tz-aware in UTC), not
    # from whatever the wire payload contained.
    from datetime import timezone as _tz

    expected_start_ts = int(
        datetime.fromisoformat(start_time).replace(tzinfo=_tz.utc).timestamp()
    )
    assert pred.prediction[0] == expected_start_ts
    assert pred.prediction[1] == simulation_input.time_increment
    assert pred.prediction[2:] == prediction[2:]


def test_get_predictions_by_request_missing_bigtable_row_returns_empty(
    db_engine: Engine,
):
    """A row whose Bigtable blob has aged out should hydrate to [], so
    downstream scoring treats it as no-prediction without crashing."""
    miner_uid = 10
    with db_engine.connect() as connection:
        with connection.begin():
            connection.execute(
                insert(Miner).values([{"miner_uid": miner_uid}])
            )

    start_time = "2026-05-25T12:00:00"
    simulation_input = SimulationInput(
        asset="BTC",
        start_time=start_time,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )
    prediction = _production_format_prediction(1, 289)

    class FakeBigtable:
        def write_predictions(self, **_):
            return {miner_uid: "bt-key-aged-out"}

        def read_predictions(self, validator_request, keys):
            return {keys[0]: []}

    handler = MinerDataHandler(db_engine, bigtable_storage=FakeBigtable())
    handler.save_responses(
        {miner_uid: (prediction, response_validation_v2.CORRECT, "1.2")},
        simulation_input,
        datetime.now(),
    )

    validator_request = handler.get_validator_requests_to_score(
        datetime.fromisoformat(start_time) + timedelta(days=2),
        7,
        86400,
        ["BTC"],
    )[0]
    result = handler.get_predictions_by_request(validator_request)
    assert result[0].prediction == []
    # Missing-row hydration must also flip format_validation away from
    # CORRECT — otherwise _crps_worker (reward.py) doesn't short-circuit
    # and the miner gets penalised as a CRPS error on an infra failure.
    from synth.validator.storage_backend import BIGTABLE_MISSING_FORMAT

    assert result[0].format_validation == BIGTABLE_MISSING_FORMAT
    assert result[0].format_validation != response_validation_v2.CORRECT


def test_prune_preserves_latest_request_per_asset_during_gap(
    db_engine: Engine,
):
    """The single newest request per asset is preserved through tapering
    even when it would otherwise be a non-keeper in its bucket — so
    low-latency downstream consumers always have the freshest predictions
    to read, even after an issuance gap that left the latest request
    older than `thin_after_minutes`.

    Scenario (mirrors the production gap on 2026-05-27, where two
    validator_requests landed in the same hourly bucket and the newer one
    was being tombstoned as a non-keeper):

        same asset, same hourly bucket, both older than `thin_after_minutes`:
          - "Older"   : start_time = now - 90 min.
          - "Latest"  : start_time = now - 50 min   (freshest per asset).

    The keeper is a randomized (salted-hash) pick, but the freshest
    request per asset is always alive after `density_tapering_predictions`:
    as the keeper itself, or — when it is a non-keeper — via the
    `latest_per_asset` clause that shields the freshest request per asset
    from the rn > 1 deletion.
    """
    now = datetime.now()
    keeper_start = now - timedelta(minutes=90)
    latest_start = now - timedelta(minutes=50)

    with db_engine.connect() as connection:
        with connection.begin():
            miner = _insert_miner(connection, miner_uid=320)
            # Older, non-latest request that contends for the bucket keeper.
            _insert_prediction(
                connection,
                miner_id=miner,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=keeper_start,
                created_at=keeper_start,
                payload=[[{"price": 1.0}]],
            )
            latest_id = _insert_prediction(
                connection,
                miner_id=miner,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=latest_start,
                created_at=latest_start,
                payload=[[{"price": 2.0}]],
            )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        latest_row = _fetch_prediction_state(connection, latest_id)

        assert latest_row is not None
        assert latest_row.deleted_at is None, (
            "Latest request per asset must stay alive even as a "
            "non-keeper, so low-latency downstream consumers can read "
            "fresh predictions during an issuance gap."
        )
        assert latest_row.prediction == [
            [{"price": 2.0}]
        ], "Latest's payload must not be replaced with the thinned tombstone."


def test_prune_drops_protection_once_latest_ages_past_time_length(
    db_engine: Engine,
):
    """The "latest per asset" protection is gated to
    `start_time > now - time_length`, so a request that has already aged
    into its scoring window can no longer stay alive — otherwise it would
    become a second scorable row in its bucket alongside the keeper.

    Scenario (long issuance outage — the latest request is stranded and
    crosses its 24h forecast window while still being the newest):

        same asset, same hourly bucket, BOTH older than 24h (`time_length`):
          - "Older"         : start_time = anchor + 5 min.
          - "Stale latest"  : start_time = anchor + 40 min (freshest, but its
                              `start_time` is older than `now - time_length`,
                              so latest_per_asset filters it out → no
                              protection).

    latest_per_asset is empty, so the bucket collapses to a single
    (randomized) keeper: exactly one of the two survives and the other is
    thinned. If the stale latest were still protected, both would stay
    alive.
    """
    now = datetime.now()
    # Pin to an hour boundary 26h ago so both requests fall in the same
    # hourly bucket and are well past the 24h `time_length` cutoff.
    anchor = (now - timedelta(hours=26)).replace(
        minute=0, second=0, microsecond=0
    )
    keeper_start = anchor + timedelta(minutes=5)
    stale_latest_start = anchor + timedelta(minutes=40)

    with db_engine.connect() as connection:
        with connection.begin():
            miner = _insert_miner(connection, miner_uid=321)
            keeper_id = _insert_prediction(
                connection,
                miner_id=miner,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=keeper_start,
                created_at=keeper_start,
                payload=[[{"price": 1.0}]],
            )
            stale_latest_id = _insert_prediction(
                connection,
                miner_id=miner,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=stale_latest_start,
                created_at=stale_latest_start,
                payload=[[{"price": 2.0}]],
            )

    MinerDataHandler(db_engine).density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        # Protection is off (latest_per_asset empty), so exactly one row —
        # the randomized keeper — survives; both surviving would mean the
        # stale latest was wrongly protected into its scoring window.
        alive, thinned = _split_alive_thinned(
            connection, [keeper_id, stale_latest_id]
        )
    assert len(alive) == 1
    assert len(thinned) == 1


class _DeleteSpyBigtable:
    """Records delete_predictions calls; other storage methods unused."""

    def __init__(self, fail: bool = False):
        self.delete_calls: list[tuple[int, list[str]]] = []
        self.fail = fail

    def delete_predictions(self, time_length: int, keys: list):
        self.delete_calls.append((time_length, sorted(keys)))
        if self.fail:
            raise RuntimeError("bigtable unavailable")


def test_density_tapering_deletes_bigtable_rows(db_engine: Engine):
    """Thinned rows' bigtable_keys are deleted from Bigtable; the keeper's
    key is not. Keeper choice is a salted hash, so expectations are derived
    from the post-run Postgres state."""
    start = (datetime.now() - timedelta(hours=25)).replace(
        minute=10, second=0, microsecond=0
    )
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=330)
            mp_ids = [
                _insert_prediction(
                    connection,
                    miner_id=miner_id,
                    asset="ETH",
                    time_length=LOW_TEST_CONFIG.time_length,
                    start_time=start + timedelta(minutes=5 * i),
                    created_at=start + timedelta(minutes=5 * i),
                    payload=[[{"price": float(i)}]],
                    bigtable_key=f"bt-key-{i}",
                )
                for i in range(3)
            ]

    fake = _DeleteSpyBigtable()
    handler = MinerDataHandler(db_engine, bigtable_storage=fake)
    handler.density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        alive, thinned = _split_alive_thinned(connection, mp_ids)
        thinned_keys = sorted(
            connection.execute(
                select(MinerPrediction.bigtable_key).where(
                    MinerPrediction.id.in_(thinned)
                )
            ).scalars()
        )
    assert len(alive) == 1
    assert len(thinned) == 2
    assert fake.delete_calls == [(LOW_TEST_CONFIG.time_length, thinned_keys)]


def test_density_tapering_skips_bigtable_without_keys(db_engine: Engine):
    """Rows without a bigtable_key (Postgres-backend rows) never reach
    delete_predictions."""
    start = (datetime.now() - timedelta(hours=25)).replace(
        minute=10, second=0, microsecond=0
    )
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=331)
            mp_ids = [
                _insert_prediction(
                    connection,
                    miner_id=miner_id,
                    asset="ETH",
                    time_length=LOW_TEST_CONFIG.time_length,
                    start_time=start + timedelta(minutes=5 * i),
                    created_at=start + timedelta(minutes=5 * i),
                    payload=[[{"price": float(i)}]],
                )
                for i in range(2)
            ]

    fake = _DeleteSpyBigtable()
    handler = MinerDataHandler(db_engine, bigtable_storage=fake)
    handler.density_tapering_predictions(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        alive, thinned = _split_alive_thinned(connection, mp_ids)
    assert len(alive) == 1
    assert len(thinned) == 1
    assert fake.delete_calls == []


def test_density_tapering_bigtable_failure_keeps_tombstones(
    db_engine: Engine,
):
    """A failed Bigtable delete must not roll back the Postgres
    tombstoning — the rows are left to the GC backstop."""
    start = (datetime.now() - timedelta(hours=25)).replace(
        minute=10, second=0, microsecond=0
    )
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=332)
            mp_ids = [
                _insert_prediction(
                    connection,
                    miner_id=miner_id,
                    asset="ETH",
                    time_length=LOW_TEST_CONFIG.time_length,
                    start_time=start + timedelta(minutes=5 * i),
                    created_at=start + timedelta(minutes=5 * i),
                    payload=[[{"price": float(i)}]],
                    bigtable_key=f"bt-key-fail-{i}",
                )
                for i in range(2)
            ]

    fake = _DeleteSpyBigtable(fail=True)
    handler = MinerDataHandler(db_engine, bigtable_storage=fake)
    handler.density_tapering_predictions(LOW_TEST_CONFIG)  # no raise

    with db_engine.connect() as connection:
        alive, thinned = _split_alive_thinned(connection, mp_ids)
    assert len(alive) == 1
    assert len(thinned) == 1
    assert len(fake.delete_calls) == 1


def test_cleanup_old_history_deletes_bigtable_rows(db_engine: Engine):
    """Rows erased by retention cleanup have their Bigtable rows deleted;
    rows still inside the retention window are untouched."""
    now = datetime.now()
    old = now - timedelta(days=LOW_TEST_CONFIG.data_retention_days + 1)
    fresh = now - timedelta(days=1)
    with db_engine.connect() as connection:
        with connection.begin():
            miner_id = _insert_miner(connection, miner_uid=333)
            old_mp = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=old,
                created_at=old,
                payload=[[{"price": 1.0}]],
                bigtable_key="bt-key-old",
            )
            fresh_mp = _insert_prediction(
                connection,
                miner_id=miner_id,
                asset="ETH",
                time_length=LOW_TEST_CONFIG.time_length,
                start_time=fresh,
                created_at=fresh,
                payload=[[{"price": 2.0}]],
                bigtable_key="bt-key-fresh",
            )

    fake = _DeleteSpyBigtable()
    handler = MinerDataHandler(db_engine, bigtable_storage=fake)
    handler.cleanup_old_history(LOW_TEST_CONFIG)

    with db_engine.connect() as connection:
        old_row = _fetch_prediction_state(connection, old_mp)
        fresh_row = _fetch_prediction_state(connection, fresh_mp)
    assert old_row.deleted_at is not None
    assert fresh_row.deleted_at is None
    assert fake.delete_calls == [(LOW_TEST_CONFIG.time_length, ["bt-key-old"])]


def test_set_miner_scores_persists_the_score_capped_flag(db_engine: Engine):
    """score_capped must survive the write into score_details_v3.

    Regression: the flag is computed in reward._build_detailed_info, but
    set_miner_scores does not store the detail dict wholesale — it copies a
    fixed list of keys into the JSONB. A flag added upstream without being
    added here is silently dropped, and the cap becomes unmonitorable.

    Also pins the back-compat default: rows scored before the cap shipped carry
    no flag, so a missing key must write False rather than raising.
    """
    handler = MinerDataHandler(db_engine)
    start_time = "2024-11-20T00:00:00+00:00"
    scored_time = datetime.fromisoformat("2024-11-22T00:00:00+00:00")

    handler, _, _ = prepare_random_predictions(db_engine, start_time)
    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["HYPE"]
    )
    assert len(validator_requests) >= 1

    with db_engine.connect() as connection:
        predictions = connection.execute(
            select(MinerPrediction.id).where(
                MinerPrediction.validator_requests_id
                == validator_requests[0].id
            )
        ).fetchall()
    assert len(predictions) >= 2

    reward_details = []
    for i, pred in enumerate(predictions):
        row = {
            "miner_uid": i,
            "miner_prediction_id": pred.id,
            "total_crps": 1.0,
            "percentile95": 1.0,
            "lowest_score": 0.01,
            "prompt_score_v3": 1.0,
            "crps_data": [{"crps": 1.0}],
        }
        # first miner capped, second explicitly not, third omits the key
        # entirely (the pre-cap shape)
        if i == 0:
            row["score_capped"] = True
        elif i == 1:
            row["score_capped"] = False
        reward_details.append(row)

    handler.set_miner_scores(
        [], int(validator_requests[0].id), reward_details, scored_time
    )

    with db_engine.connect() as connection:
        results = connection.execute(
            select(MinerScore.score_details_v3)
            .where(
                MinerScore.miner_predictions_id.in_(
                    [p.id for p in predictions]
                )
            )
            .order_by(MinerScore.miner_predictions_id)
        ).fetchall()

    assert results[0][0]["score_capped"] is True
    assert results[1][0]["score_capped"] is False
    if len(results) > 2:
        # omitted upstream -> stored as False, never missing and never an error
        assert results[2][0]["score_capped"] is False

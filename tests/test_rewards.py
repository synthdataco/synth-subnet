import numpy as np
import pytest
from numpy.testing import assert_almost_equal
from datetime import datetime, timedelta
from multiprocessing import shared_memory

from sqlalchemy import delete

from synth.db.models import MinerPrediction, ValidatorRequest, MinerScore
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator.reward import (
    _crps_worker,
    compute_prompt_scores,
    compute_softmax,
    get_rewards_multiprocess,
)
from synth.validator import competition_config
from tests.utils import prepare_random_predictions, recent_start_time


@pytest.fixture(scope="function", autouse=True)
def setup_data(db_engine):
    with db_engine.connect() as connection:
        with connection.begin():
            connection.execute(delete(MinerScore))
            connection.execute(delete(MinerPrediction))
            connection.execute(delete(ValidatorRequest))


def test_compute_softmax_1():
    score_values = np.array([1000, 1500, 2000])
    expected_score = np.array([0.506, 0.307, 0.186])

    actual_score = compute_softmax(score_values, -0.001)

    assert_almost_equal(actual_score, expected_score, decimal=3)


def test_compute_softmax_2():
    score_values = np.array([1000, 1500, 2000, -1])
    expected_score = np.array([0.213, 0.129, 0.078, 0.58])

    actual_score = compute_softmax(score_values, -0.001)

    assert_almost_equal(actual_score, expected_score, decimal=3)


def test_compute_prompt_scores():
    crps_scores = np.array([1000, 1500, 2000, -1])
    # Valid scores are not capped; the miss (-1) is filled with the 95th
    # percentile of the valid scores (1950), then shifted by the minimum.
    expected_prompt_scores = np.array([0, 500, 1000, 950])

    actual_score, percentile95, lowest_score = compute_prompt_scores(
        crps_scores
    )

    assert percentile95 == 1950.0
    assert lowest_score == 1000
    assert np.array_equal(actual_score, expected_prompt_scores)


def test_compute_prompt_scores_only_one_miner():
    crps_scores = np.array([1000, -1, -1, -1])
    expected_prompt_scores = np.array(
        [0, 0, 0, 0]
    )  # TODO: not ideal but it's the current behavior

    actual_score, percentile95, lowest_score = compute_prompt_scores(
        crps_scores
    )

    assert percentile95 == 1000
    assert lowest_score == 1000
    assert np.array_equal(actual_score, expected_prompt_scores)


def test_crps_worker_drops_non_finite_detailed_data():
    # A path whose relative change overflows float64 makes the CRPS
    # non-finite. The worker must not hand that back as detailed data:
    # set_miner_scores writes it as JSONB, which has no NaN/Infinity.
    real_prices = np.array([100.0, 101.0, 102.0, 103.0])
    shm = shared_memory.SharedMemory(create=True, size=real_prices.nbytes)
    try:
        shared_prices = np.ndarray(
            real_prices.shape, dtype=np.float64, buffer=shm.buf
        )
        shared_prices[:] = real_prices[:]

        prediction = [
            int(datetime.now().timestamp()),
            300,
            [100.0, 5e-324, 1e300, 103.0],
            [100.0, 101.0, 102.0, 103.0],
        ]

        (
            miner_uid,
            score,
            detailed_crps_data,
            error,
            _,
            _,
            _,
        ) = _crps_worker(
            (
                42,
                prediction,
                shm.name,
                real_prices.shape,
                300,
                {"5min": 300, "20min_abs": 1200},
                "CORRECT",
                1,
                0.0,
            )
        )
    finally:
        shm.close()
        shm.unlink()

    assert miner_uid == 42
    assert score == -1
    assert detailed_crps_data == []
    assert "non-finite score" in error


def test_get_rewards(db_engine):
    start_time = recent_start_time()
    scored_time = datetime.fromisoformat(start_time) + timedelta(
        hours=24, minutes=5
    )

    handler, _, miner_uids = prepare_random_predictions(db_engine, start_time)

    price_data_provider = PriceDataProvider()

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7, 86400, ["HYPE"]
    )

    prompt_scores, detailed_info, real_prices = get_rewards_multiprocess(
        handler,
        price_data_provider,
        validator_requests[0],
        competition_config.CRYPTO_24H,
    )

    assert prompt_scores is not None

    percentile95 = detailed_info[0]["percentile95"]

    # find the lowest and highest valid crps values (-1 is invalid)
    valid_crps = [
        item["total_crps"]
        for item in detailed_info
        if item["total_crps"] != -1
    ]
    lowest_crps = min(valid_crps)
    highest_crps = max(valid_crps)

    assert len(prompt_scores) == len(miner_uids)
    assert min(prompt_scores) == 0

    # valid scores are uncapped: the max score is the highest valid crps
    # minus the lowest (misses are filled with percentile95, which never
    # exceeds the highest valid crps)
    assert max(prompt_scores) == highest_crps - lowest_crps
    assert percentile95 <= highest_crps

    assert detailed_info[0]["miner_uid"] == miner_uids[0]
    crps_data = detailed_info[0]["crps_data"]
    assert all(d["Increment"] == "Total" for d in crps_data)
    assert crps_data[-1]["Interval"] == "Overall"
    assert real_prices is not None
    assert len(real_prices) == 289

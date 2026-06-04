import numpy as np
import pytest
from numpy.testing import assert_almost_equal
from datetime import datetime, timedelta

from sqlalchemy import delete

from synth.db.models import MinerPrediction, ValidatorRequest, MinerScore
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator.reward import (
    compute_prompt_scores,
    compute_softmax,
    get_rewards_multiprocess,
)
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
    expected_prompt_scores = np.array([0, 500, 900, 900])

    actual_score, percentile90, lowest_score = compute_prompt_scores(
        crps_scores
    )

    assert percentile90 == 1900.0
    assert lowest_score == 1000
    assert np.array_equal(actual_score, expected_prompt_scores)


def test_compute_prompt_scores_only_one_miner():
    crps_scores = np.array([1000, -1, -1, -1])
    expected_prompt_scores = np.array(
        [0, 0, 0, 0]
    )  # TODO: not ideal but it's the current behavior

    actual_score, percentile90, lowest_score = compute_prompt_scores(
        crps_scores
    )

    assert percentile90 == 1000
    assert lowest_score == 1000
    assert np.array_equal(actual_score, expected_prompt_scores)


def test_get_rewards(db_engine):
    start_time = recent_start_time()
    scored_time = datetime.fromisoformat(start_time) + timedelta(
        hours=24, minutes=5
    )

    handler, _, miner_uids = prepare_random_predictions(db_engine, start_time)

    price_data_provider = PriceDataProvider()

    validator_requests = handler.get_validator_requests_to_score(
        scored_time, 7
    )

    prompt_scores, detailed_info, real_prices = get_rewards_multiprocess(
        handler,
        price_data_provider,
        validator_requests[0],
    )

    assert prompt_scores is not None

    percentile90 = detailed_info[0]["percentile90"]

    # find the lowest crps value
    crps_values = [item["total_crps"] for item in detailed_info]
    lowest_crps = float("inf")
    for crps in crps_values:
        # -1 is an invalid prediction
        if crps < lowest_crps and crps != -1:
            lowest_crps = crps

    assert len(prompt_scores) == len(miner_uids)
    assert min(prompt_scores) == 0

    # the max score is the percentile90 - lowest_crps
    assert max(prompt_scores) == percentile90 - lowest_crps

    assert detailed_info[0]["miner_uid"] == miner_uids[0]
    assert len(detailed_info[0]["crps_data"]) == 5
    assert real_prices is not None
    assert len(real_prices) == 289

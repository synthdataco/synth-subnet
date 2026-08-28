import numpy as np
import pytest
from numpy.testing import assert_almost_equal
from datetime import datetime, timedelta
from multiprocessing import shared_memory

from sqlalchemy import delete

from synth.db.models import MinerPrediction, ValidatorRequest, MinerScore
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator.reward import (
    OUTLIER_SCORE_MEDIAN_MULTIPLE,
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

    actual_score, percentile95, lowest_score, _ = compute_prompt_scores(
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

    actual_score, percentile95, lowest_score, _ = compute_prompt_scores(
        crps_scores
    )

    assert percentile95 == 1000
    assert lowest_score == 1000
    assert np.array_equal(actual_score, expected_prompt_scores)


def test_absurd_response_does_not_poison_the_fill_for_missed_responses():
    """A minority of absurd responses must not poison the fill for a miss.

    Prices are only rejected above the float32 ceiling, so an accepted response
    can carry an enormous CRPS. Uncapped, a small minority of those pulls the
    p95 up — and p95 is what fills a MISSED response, so a miner that merely
    timed out inherits the value, exp(beta * score) underflows to exactly 0, and
    compute_smoothed_score drops it from the rewards list for the whole window.

    The miss must come back with a bad-but-survivable score, not an
    astronomical one.
    """
    # A legitimate field with real spread, a minority of absurd responses, then
    # one miner that missed.
    legit = [20.0, 22.0, 25.0, 28.0, 30.0, 33.0, 36.0, 40.0, 45.0, 50.0, 60.0]
    field = legit + [1e12, 1e12] + [-1.0]
    scores, percentile95, lowest, capped = compute_prompt_scores(
        np.array(field)
    )

    # The fill is the 95th percentile of the miners who actually ANSWERED, so
    # it sits inside the legitimate range — not on the cap, and nowhere near
    # the garbage that caused the incident.
    assert min(legit) <= percentile95 <= max(legit)

    # The miner that missed is scored like a bad-but-real miner...
    missed = scores[-1]
    assert missed == pytest.approx(percentile95 - lowest)
    assert missed <= max(legit) - min(legit)
    # ...while the two that submitted garbage are capped and ranked last.
    assert scores[11] == scores[12] > missed

    # What actually decides whether a miner is paid: the prompt is averaged
    # into a ~10-day window. Uncapped, one poisoned row dominated that mean and
    # exp(beta * mean) underflowed to a hard zero, which compute_smoothed_score
    # drops from the rewards list entirely.
    window = 1900

    def mean_with(row):
        return (row + (window - 1) * 30.0) / window

    assert compute_softmax(np.array([mean_with(missed)]), -0.15)[0] > 0
    # ...and the uncapped value is exactly what that guards against.
    assert np.exp(-0.15 * mean_with(1e12)) == 0.0


def test_outlier_is_clipped_but_ordinary_bad_scores_are_untouched():
    """The cap separates a real response from an impossible one.

    It is not the p90 cap removed in #302, which compressed ordinary scores.
    Responses beyond 10x their prompt's median are vanishingly rare in real
    scoring history, so nothing ordinary is touched here.
    """
    median = 30.0
    cap = median * OUTLIER_SCORE_MEDIAN_MULTIPLE
    # 5x the median is a genuinely bad but entirely real response.
    field = np.array([median] * 10 + [median * 5, 1e12, 1e30])
    scores, _, lowest, capped = compute_prompt_scores(field)

    # The legitimately-bad miner keeps its real score, undistorted and unflagged.
    assert scores[10] == pytest.approx(median * 5 - lowest)
    assert not capped[10]
    # Both garbage responses land on the cap, tied at "worst", and are flagged.
    assert scores[11] == pytest.approx(cap - lowest)
    assert scores[11] == scores[12]
    assert capped[11] and capped[12]
    # The flag is what makes the cap monitorable; only the garbage carries it.
    assert capped.sum() == 2


def test_capped_flag_is_aligned_with_the_input_and_excludes_misses():
    """The flag is persisted per row, so it must line up with score_values.

    A miss (-1) is below any positive cap and must never be flagged as capped.
    """
    field = np.array([10.0, 12.0, 1e9, -1.0, 11.0])
    _, _, _, capped = compute_prompt_scores(field)

    assert capped.shape == field.shape
    assert list(capped) == [False, False, True, False, False]


def test_all_absurd_field_does_not_divide_by_a_zero_median():
    # Degenerate guard: a field with a zero median must not produce a zero cap
    # that clips every score to nothing.
    scores, percentile95, _, _ = compute_prompt_scores(
        np.array([0.0, 0.0, 0.0, 5.0])
    )
    assert np.all(np.isfinite(scores))
    assert percentile95 == pytest.approx(4.25)


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

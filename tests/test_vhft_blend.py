"""Unit tests for the VHFT blend in moving_average.compute_vhft_smoothed_score.

Covers the concentration guard and the equal-split invariant. The DB is a
MagicMock — only get_miner_uid_to_id_map() is consulted.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from synth.validator.competition_config import (
    SMOOTHED_SCORE_COEFFICIENT,
    VHFT_COMPETITION,
    VHFT_MAX_PARTICIPANTS,
    VHFT_MIN_PARTICIPANTS,
)
from synth.validator.moving_average import compute_vhft_smoothed_score

SCORED_TIME = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _handler(registered_uids):
    """MinerDataHandler stub mapping each uid to a distinct miner_id."""
    handler = MagicMock()
    handler.get_miner_uid_to_id_map.return_value = {
        uid: 1000 + uid for uid in registered_uids
    }
    return handler


def _blend(vhft_scores, registered_uids=None):
    if registered_uids is None:
        registered_uids = list(vhft_scores)
    return compute_vhft_smoothed_score(
        _handler(registered_uids),
        vhft_scores,
        SCORED_TIME,
        VHFT_COMPETITION,
    )


def test_steady_state_field_blends_and_splits_the_block_evenly():
    scores = {uid: 0.4 for uid in range(9)}
    rewards = _blend(scores)

    assert rewards is not None
    assert len(rewards) == 9
    # The invariant the equal 4-way emissions split rests on: every competition
    # contributes exactly SMOOTHED_SCORE_COEFFICIENT, so after set_weights
    # L1-normalizes, four competitions land at 25% each.
    total = sum(r["reward_weight"] for r in rewards)
    assert total == pytest.approx(SMOOTHED_SCORE_COEFFICIENT)
    assert all(r["prompt_name"] == VHFT_COMPETITION.label for r in rewards)


def test_lower_crps_earns_more_than_higher_crps():
    # softmax_beta must stay negative: VHFT is lower-is-better like the rest.
    rewards = _blend({1: 0.1, 2: 0.4, 3: 5.0})
    by_uid = {r["miner_uid"]: r["reward_weight"] for r in rewards}
    assert by_uid[1] > by_uid[2] > by_uid[3]


@pytest.mark.parametrize("n", [1, VHFT_MIN_PARTICIPANTS - 1])
def test_too_few_participants_skips_the_cycle(n):
    # The block is a fixed share however many split it, so at n=1 a single
    # miner would take the whole 25% of emissions.
    assert _blend({uid: 0.4 for uid in range(n)}) is None


def test_too_many_participants_skips_the_cycle():
    n = VHFT_MAX_PARTICIPANTS + 1
    assert _blend({uid: 0.4 for uid in range(n)}) is None


def test_guard_counts_paid_miners_not_returned_uids():
    """Regression: the guard runs AFTER the uid -> miner_id mapping.

    A field of 9 scored uids of which only 2 are registered concentrates
    exactly as hard as a field of 2, so counting the raw scorer response would
    miss it.
    """
    scores = {uid: 0.4 for uid in range(9)}
    assert _blend(scores, registered_uids=[0, 1]) is None


def test_unregistered_uids_are_dropped_but_a_valid_field_still_blends():
    scores = {uid: 0.4 for uid in range(9)}
    registered = list(range(5))
    rewards = _blend(scores, registered_uids=registered)

    assert rewards is not None
    assert {r["miner_uid"] for r in rewards} == set(registered)
    # Dropping uids does not leak emissions: the block still sums to its share.
    total = sum(r["reward_weight"] for r in rewards)
    assert total == pytest.approx(SMOOTHED_SCORE_COEFFICIENT)


def test_returns_none_when_there_is_nothing_to_blend():
    assert _blend({}) is None


def test_returns_none_when_the_identity_map_is_unavailable():
    handler = MagicMock()
    handler.get_miner_uid_to_id_map.return_value = None
    assert (
        compute_vhft_smoothed_score(
            handler, {1: 0.4}, SCORED_TIME, VHFT_COMPETITION
        )
        is None
    )

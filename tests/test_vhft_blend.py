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
    VHFT_ASSETS,
    VHFT_MIN_PARTICIPANTS,
    competition_shares,
    vhft_competition,
)
from synth.validator.moving_average import compute_vhft_smoothed_score

SCORED_TIME = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _field(n):
    """A plausible scored field of n uids, with a DISTINCT score each.

    Deliberately not a flat field. Scores are field-relative (each prompt's winner
    scores 0), and a real one is never all-equal over a full window — an identical
    field is the signature of a placeholder/degraded snapshot, which
    VhftScoreProvider.fetch_scores rejects outright. These tests call
    compute_vhft_smoothed_score directly and so bypass that guard, but they should not
    ship an example of the exact shape it exists to reject.
    """
    return {uid: 0.4 + uid / 100 for uid in range(n)}


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
    scores = _field(9)
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
    assert _blend(_field(n)) is None


def test_too_many_participants_skips_the_cycle():
    n = VHFT_MAX_PARTICIPANTS + 1
    assert _blend(_field(n)) is None


def test_guard_counts_paid_miners_not_returned_uids():
    """Regression: the guard runs AFTER the uid -> miner_id mapping.

    A field of 9 scored uids of which only 2 are registered concentrates
    exactly as hard as a field of 2, so counting the raw scorer response would
    miss it.
    """
    scores = _field(9)
    assert _blend(scores, registered_uids=[0, 1]) is None


def test_unregistered_uids_are_dropped_but_a_valid_field_still_blends():
    scores = _field(9)
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


def test_each_asset_gets_its_share_of_the_block():
    # Four rewarded assets: each competition pays a quarter of the block, so
    # together they still sum to one competition's SMOOTHED_SCORE_COEFFICIENT.
    rewards = compute_vhft_smoothed_score(
        _handler(range(9)),
        _field(9),
        SCORED_TIME,
        vhft_competition("ETH"),
        share=0.25,
    )
    total = sum(r["reward_weight"] for r in rewards)
    assert total == pytest.approx(SMOOTHED_SCORE_COEFFICIENT / 4)
    assert {r["prompt_name"] for r in rewards} == {"VHFT 10s ETH"}


def test_btc_keeps_the_label_its_rewards_are_stored_under():
    # miner_rewards.prompt_name has said "VHFT 10s" since before other assets.
    assert vhft_competition("BTC") is VHFT_COMPETITION
    assert VHFT_COMPETITION.label == "VHFT 10s"
    eth = vhft_competition("ETH")
    assert eth.asset_list == ["ETH"]


def test_each_asset_has_its_own_beta():
    # Scores are in each asset's price units, so the cheaper the asset, the
    # steeper its beta has to be for the same separation between miners.
    betas = [vhft_competition(a).softmax_beta for a in VHFT_ASSETS]
    assert betas[0] == VHFT_COMPETITION.softmax_beta
    assert all(b < 0 for b in betas)
    assert betas == sorted(betas, reverse=True)  # BTC, ETH, SOL, XRP: steeper


class _Provider:
    def __init__(self, fields):
        self.assets = list(fields)
        self._fields = fields

    def fetch_scores(self, asset="BTC"):
        return self._fields[asset]


def test_the_cycle_pays_each_rewarded_asset_as_its_own_competition(
    monkeypatch,
):
    from synth.validator import competition_config, forward

    monkeypatch.setattr(competition_config, "ALL_COMPETITIONS", [])
    handler = _handler(range(9))
    provider = _Provider(
        {"BTC": _field(9), "ETH": _field(9), "SOL": None, "XRP": _field(9)}
    )

    combined = forward.calculate_moving_average_and_update_rewards(
        handler, SCORED_TIME, vhft_provider=provider
    )

    written = [
        call.args[0][0]["prompt_name"]
        for call in handler.update_miner_rewards.call_args_list
    ]
    assert written == ["VHFT 10s", "VHFT 10s ETH", "VHFT 10s XRP"]
    # Each rewarded asset is a quarter of the block; SOL had no snapshot this
    # cycle, so its quarter goes unpaid rather than to the others.
    total = sum(r["reward_weight"] for r in combined)
    assert total == pytest.approx(SMOOTHED_SCORE_COEFFICIENT * 3 / 4)


def test_expected_shares_follow_the_rewarded_assets_not_the_ones_seen():
    # Four rewarded, one skipped this cycle: the others still expect a quarter
    # of the VHFT share each, not a third.
    four = competition_shares(["BTC", "ETH", "SOL", "XRP"])
    assert four["VHFT 10s ETH"] == pytest.approx(
        SMOOTHED_SCORE_COEFFICIENT / 4
    )
    assert sum(four.values()) == pytest.approx(1.0)
    # BTC alone (the default) gets the whole VHFT share; the others are not
    # expected at all.
    btc = competition_shares(["BTC"])
    assert btc["VHFT 10s"] == pytest.approx(SMOOTHED_SCORE_COEFFICIENT)
    assert "VHFT 10s ETH" not in btc
    # without VHFT, only the inline competitions
    assert not any(
        label.startswith("VHFT") for label in competition_shares([])
    )

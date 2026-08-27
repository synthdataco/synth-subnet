"""Unit tests for vhft_score_provider.py — no DB or network required.

Covers the /v1/scores parsing contract: only rows with positive weight are kept
(scores are field-relative, so 0.0 is a legitimate winning score and must NOT be
filtered), a null or negative mean_crps is dropped rather than coerced, an
all-identical field is rejected as a placeholder snapshot, and every failure mode
returns None so the scoring cycle skips VHFT instead of raising.
"""

from unittest.mock import MagicMock, patch

import pytest

from synth.validator.vhft_score_provider import VhftScoreProvider


def _response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _fetch(payload):
    with patch(
        "synth.validator.vhft_score_provider.requests.get",
        return_value=_response(payload),
    ):
        return VhftScoreProvider(url="http://vhft/v1/scores").fetch_scores()


def test_from_env_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("VHFT_SCORES_URL", raising=False)
    assert VhftScoreProvider.from_env() is None


def test_from_env_enabled_when_set(monkeypatch):
    monkeypatch.setenv("VHFT_SCORES_URL", "http://vhft/v1/scores")
    provider = VhftScoreProvider.from_env()
    assert provider is not None
    assert provider._url == "http://vhft/v1/scores"


def test_keeps_only_positive_weight_rows():
    # Non-participants come back as weight=0.0/mean_crps=0.0; keeping them would
    # read as a perfect score to the lower-is-better softmax downstream.
    scores = _fetch(
        {
            "scores": [
                {"uid": 12, "weight": 0.5, "mean_crps": 22.0},
                {"uid": 13, "weight": 0.0, "mean_crps": 0.0},
                {"uid": 14, "weight": 0.5, "mean_crps": 25.5},
            ]
        }
    )
    assert scores == {12: 22.0, 14: 25.5}


def test_null_mean_crps_is_dropped_not_coerced():
    # Regression: float(None) used to raise TypeError outside the try block,
    # crashing the scoring cycle instead of skipping VHFT. null is the scorer's
    # deliberate "no CRPS" sentinel (0.0 is a real perfect score).
    scores = _fetch(
        {
            "scores": [
                {"uid": 12, "weight": 0.5, "mean_crps": None},
                {"uid": 14, "weight": 0.5, "mean_crps": 25.5},
            ]
        }
    )
    assert scores == {14: 25.5}


def test_null_weight_is_dropped_not_coerced():
    scores = _fetch(
        {
            "scores": [
                {"uid": 12, "weight": None, "mean_crps": 22.0},
                {"uid": 14, "weight": 0.5, "mean_crps": 25.5},
            ]
        }
    )
    assert scores == {14: 25.5}


def test_malformed_row_is_dropped():
    scores = _fetch(
        {
            "scores": [
                {"weight": 0.5, "mean_crps": 22.0},  # no uid
                {"uid": "not-an-int", "weight": 0.5, "mean_crps": 22.0},
                {"uid": 14, "weight": 0.5, "mean_crps": 25.5},
            ]
        }
    )
    assert scores == {14: 25.5}


def test_bootstrap_snapshot_is_rejected():
    """Regression: a post-restart placeholder snapshot must not read as a full
    field of perfect scores.

    After a restart /v1/scores can serve every uid at a nonzero weight with
    mean_crps=0.0, written before the first real scoring round. Filtering on
    weight alone let all of them through, and 0.0 is the *best* possible value
    to the lower-is-better softmax, so the VHFT block's whole share of emissions
    would be spread across every registered miner — none of whom competed.

    Now caught by the degenerate-snapshot guard (all scores identical) rather than
    by rejecting 0.0, which is a legitimate winning score under field-relative
    scoring. VHFT_MAX_PARTICIPANTS=64 backs this up for the 256-uid case.
    """
    bootstrap = {
        "scores": [
            {"uid": uid, "weight": 1 / 256, "mean_crps": 0.0}
            for uid in range(256)
        ]
    }
    assert _fetch(bootstrap) is None


def test_zero_is_kept_as_the_winner_and_negative_is_dropped():
    """0.0 is the BEST field-relative score, not a bootstrap artifact.

    Scores are now relative to each prompt's winner, so the best miner in the
    window legitimately averages 0.0. Dropping it (the old rule) would exclude
    the winner from every cycle. A negative score is still impossible and dropped.
    """
    scores = _fetch(
        {
            "scores": [
                {"uid": 12, "weight": 0.5, "mean_crps": 0.0},   # winner — kept
                {"uid": 13, "weight": 0.5, "mean_crps": 22.0},
                {"uid": 14, "weight": 0.5, "mean_crps": -1.0},  # impossible — dropped
                {"uid": 15, "weight": 0.5, "mean_crps": 25.5},
            ]
        }
    )
    assert scores == {12: 0.0, 13: 22.0, 15: 25.5}


def test_identical_nonzero_field_is_rejected_as_a_placeholder():
    """The degenerate-snapshot guard is not specific to 0.0.

    A real 24h field-relative field is never all-equal, so an identical field at
    ANY value is a placeholder/degraded snapshot rather than a result.
    """
    assert (
        _fetch(
            {
                "scores": [
                    {"uid": uid, "weight": 0.25, "mean_crps": 7.5}
                    for uid in (12, 13, 14, 15)
                ]
            }
        )
        is None
    )


def test_single_scored_uid_is_not_treated_as_degenerate():
    """A one-miner field is trivially 'all identical' — the guard needs >1 to fire.

    Whether one participant is enough is compute_vhft_smoothed_score's call
    (VHFT_MIN_PARTICIPANTS), not this parser's.
    """
    assert _fetch({"scores": [{"uid": 12, "weight": 1.0, "mean_crps": 0.0}]}) == {12: 0.0}


def test_steady_state_field_is_accepted():
    # The shape the guard must NOT reject: a handful of scored uids, the rest
    # at weight 0.
    participants = {3, 17, 41, 68, 90, 114, 137, 160, 191}
    payload = {
        "scores": [
            {
                "uid": uid,
                "weight": 0.11 if uid in participants else 0.0,
                # Distinct per uid: a real field-relative field is never all-equal,
                # and an all-equal one is deliberately rejected as a placeholder.
                "mean_crps": (0.4 + uid / 1000) if uid in participants else 0.0,
            }
            for uid in range(256)
        ]
    }
    scores = _fetch(payload)
    assert scores is not None
    assert set(scores) == participants


@pytest.mark.parametrize(
    "payload",
    [
        {"scores": []},
        {},
        {"scores": [{"uid": 12, "weight": 0.0, "mean_crps": 0.0}]},
        {"scores": [{"uid": 12, "weight": 0.5, "mean_crps": None}]},
        # A negative score is impossible by construction and still unusable. (0.0 is
        # NOT here any more: under field-relative scoring it is the winning score.)
        {"scores": [{"uid": 12, "weight": 0.5, "mean_crps": -1.0}]},
    ],
)
def test_returns_none_when_nothing_usable(payload):
    assert _fetch(payload) is None


def test_returns_none_on_http_failure():
    with patch(
        "synth.validator.vhft_score_provider.requests.get",
        side_effect=Exception("connection refused"),
    ):
        provider = VhftScoreProvider(url="http://vhft/v1/scores")
        assert provider.fetch_scores() is None

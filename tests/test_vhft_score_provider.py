"""Unit tests for vhft_score_provider.py — no DB or network required.

Covers the /v1/scores parsing contract: only rows with positive weight AND
positive mean_crps are kept, a null mean_crps is dropped rather than coerced,
and every failure mode returns None so the scoring cycle skips VHFT instead of
raising.
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
    """
    bootstrap = {
        "scores": [
            {"uid": uid, "weight": 1 / 256, "mean_crps": 0.0}
            for uid in range(256)
        ]
    }
    assert _fetch(bootstrap) is None


def test_zero_crps_row_is_dropped_but_healthy_peers_survive():
    # A partial bootstrap/degraded snapshot must not poison the whole cycle:
    # drop only the 0.0 rows and keep scoring the real participants.
    scores = _fetch(
        {
            "scores": [
                {"uid": 12, "weight": 0.5, "mean_crps": 0.0},
                {"uid": 13, "weight": 0.5, "mean_crps": 22.0},
                {"uid": 14, "weight": 0.5, "mean_crps": -1.0},
                {"uid": 15, "weight": 0.5, "mean_crps": 25.5},
            ]
        }
    )
    assert scores == {13: 22.0, 15: 25.5}


def test_steady_state_field_is_accepted():
    # The shape the guard must NOT reject: a handful of scored uids, the rest
    # at weight 0.
    participants = {3, 17, 41, 68, 90, 114, 137, 160, 191}
    payload = {
        "scores": [
            {
                "uid": uid,
                "weight": 0.11 if uid in participants else 0.0,
                "mean_crps": 0.4 if uid in participants else 0.0,
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
        {"scores": [{"uid": 12, "weight": 0.5, "mean_crps": 0.0}]},
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

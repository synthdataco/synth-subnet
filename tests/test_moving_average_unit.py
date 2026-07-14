"""Unit tests for moving_average.py — no DB required.

Covers:
- prepare_df_for_moving_average
-
- compute_smoothed_score
- combine_moving_averages

These tests use synthetic data and mock the DB-dependent calls so they
run fast and without testcontainers.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from synth.validator.moving_average import (
    ASSET_COEFFICIENTS,
    combine_moving_averages,
    compute_smoothed_score,
    prepare_df_for_moving_average,
)
from synth.validator.competition_config import (
    COM_EQU_24H,
    CRYPTO_1H,
    CRYPTO_24H,
    SMOOTHED_SCORE_COEFFICIENT,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(minutes_offset: int, base: datetime | None = None) -> datetime:
    """Return a UTC datetime offset by `minutes_offset` from base."""
    if base is None:
        base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes_offset)


def _make_scores_df(
    miner_ids: list[int],
    times: list[datetime],
    assets: list[str] | None = None,
    scores: list[float] | None = None,
    percentile95: float = 0.01,
    lowest_score: float = 0.0,
    sparse_entries: list[tuple[int, int]] | None = None,
) -> pd.DataFrame:
    """Build a scores DataFrame similar to get_miner_scores output.

    If sparse_entries is given, only those (miner_id_index, time_index) pairs
    are included — useful for simulating miners joining late.
    """
    rows = []
    if sparse_entries is not None:
        for mi, ti in sparse_entries:
            mid = miner_ids[mi]
            t = times[ti]
            asset = (assets or ["BTC"])[ti % len(assets or ["BTC"])]
            score = scores[len(rows) % len(scores)] if scores else 0.005
            rows.append(
                {
                    "miner_id": mid,
                    "prompt_score_v3": score,
                    "scored_time": t,
                    "asset": asset,
                    "percentile95": percentile95,
                    "lowest_score": lowest_score,
                }
            )
    else:
        for ti, t in enumerate(times):
            asset = (assets or ["BTC"])[ti % len(assets or ["BTC"])]
            for mi, mid in enumerate(miner_ids):
                score = scores[mi] if scores else 0.001 * (mi + 1)
                rows.append(
                    {
                        "miner_id": mid,
                        "prompt_score_v3": score,
                        "scored_time": t,
                        "asset": asset,
                        "percentile95": percentile95,
                        "lowest_score": lowest_score,
                    }
                )
    df = pd.DataFrame(rows)
    df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
    return df


def _mock_handler(miner_id_to_uid: dict[int, int]) -> MagicMock:
    """Return a mock MinerDataHandler that maps miner_id -> miner_uid."""
    handler = MagicMock()

    def populate(miner_data):
        for item in miner_data:
            item["miner_uid"] = miner_id_to_uid.get(item["miner_id"])
        return miner_data

    handler.populate_miner_uid_in_miner_data.side_effect = populate
    return handler


# ===========================================================================
# Tests for prepare_df_for_moving_average
# ===========================================================================


class TestPrepareDfForMovingAverage:
    """Tests for prepare_df_for_moving_average."""

    def test_basic_all_miners_present_at_all_times(self):
        """When all miners have scores at all times, output matches input (minus extra cols)."""
        times = [_ts(0), _ts(60), _ts(120)]
        df = _make_scores_df([1, 2, 3], times, assets=["BTC", "ETH", "BTC"])
        result = prepare_df_for_moving_average(df)

        assert set(result.columns) == {
            "scored_time",
            "miner_id",
            "prompt_score_v3",
            "asset",
        }
        # 3 miners × 3 times = 9 rows, no backfill needed
        assert len(result) == 9
        assert result["prompt_score_v3"].isna().sum() == 0

    def test_new_miner_gets_backfilled(self):
        """A miner appearing only at later times gets worst-score backfill for earlier times."""
        times = [_ts(0), _ts(60), _ts(120)]
        miner_ids = [1, 2]
        # Miner 1 present at all times, miner 2 only at t=120
        entries = [
            (0, 0),
            (0, 1),
            (0, 2),  # miner 1 at all times
            (1, 2),  # miner 2 only at t=120
        ]
        df = _make_scores_df(
            miner_ids,
            times,
            assets=["BTC", "ETH", "SOL"],
            scores=[0.005],
            sparse_entries=entries,
        )

        result = prepare_df_for_moving_average(df)

        # Miner 2 should have backfilled rows at t=0 and t=60
        miner2 = result[result["miner_id"] == 2].sort_values("scored_time")
        assert len(miner2) == 3  # present at all 3 times now

        # Backfilled scores should be percentile95 - lowest_score = 0.01 - 0.0 = 0.01
        backfilled = miner2[miner2["scored_time"] < _ts(120)]
        assert len(backfilled) == 2
        for _, row in backfilled.iterrows():
            assert row["prompt_score_v3"] == pytest.approx(0.01)

    def test_old_miner_not_backfilled(self):
        """A miner present from the start with missing scores does NOT get backfilled."""
        times = [_ts(0), _ts(60), _ts(120)]
        miner_ids = [1, 2]
        # Miner 1 at t=0 only, miner 2 at all times
        entries = [
            (0, 0),  # miner 1 at t=0 only
            (1, 0),
            (1, 1),
            (1, 2),  # miner 2 at all times
        ]
        df = _make_scores_df(
            miner_ids,
            times,
            assets=["BTC", "ETH", "SOL"],
            scores=[0.005],
            sparse_entries=entries,
        )

        result = prepare_df_for_moving_average(df)

        # Miner 1 should only have 1 row (the real one), not backfilled
        miner1 = result[result["miner_id"] == 1]
        assert len(miner1) == 1

    def test_output_columns(self):
        """Output has exactly the expected columns."""
        df = _make_scores_df([1], [_ts(0)], assets=["BTC"])
        result = prepare_df_for_moving_average(df)
        assert list(sorted(result.columns)) == [
            "asset",
            "miner_id",
            "prompt_score_v3",
            "scored_time",
        ]

    def test_output_sorted(self):
        """Output is sorted by scored_time, miner_id."""
        times = [_ts(0), _ts(60)]
        df = _make_scores_df([3, 1, 2], times, assets=["BTC", "ETH"])
        result = prepare_df_for_moving_average(df)

        scored_times = result["scored_time"].tolist()
        assert scored_times == sorted(scored_times)

        for t in result["scored_time"].unique():
            subset = result[result["scored_time"] == t]
            assert subset["miner_id"].tolist() == sorted(
                subset["miner_id"].tolist()
            )

    def test_miner_id_is_int(self):
        """miner_id column should be int type."""
        df = _make_scores_df([1, 2], [_ts(0)], assets=["BTC"])
        result = prepare_df_for_moving_average(df)
        assert result["miner_id"].dtype in [np.int64, np.int32, int]

    def test_single_time_single_miner(self):
        """Edge case: one miner, one time."""
        df = _make_scores_df([42], [_ts(0)], assets=["BTC"])
        result = prepare_df_for_moving_average(df)
        assert len(result) == 1
        assert result.iloc[0]["miner_id"] == 42

    def test_multiple_assets_at_same_time(self):
        """Scores from different assets at the same scored_time should all be preserved."""
        t = _ts(0)
        rows = [
            {
                "miner_id": 1,
                "prompt_score_v3": 0.01,
                "scored_time": t,
                "asset": "BTC",
                "percentile95": 0.02,
                "lowest_score": 0.0,
            },
            {
                "miner_id": 1,
                "prompt_score_v3": 0.02,
                "scored_time": t,
                "asset": "ETH",
                "percentile95": 0.02,
                "lowest_score": 0.0,
            },
        ]
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
        result = prepare_df_for_moving_average(df)
        assert len(result) == 2

    def test_preserves_real_scores_over_backfill(self):
        """When a new miner has a real score, it should not be overwritten by backfill."""
        times = [_ts(0), _ts(60)]
        miner_ids = [1, 2]
        # Miner 2 is new (only at t=60) with a specific score
        entries = [(0, 0), (0, 1), (1, 1)]
        df = _make_scores_df(
            miner_ids,
            times,
            assets=["BTC", "ETH"],
            scores=[0.003],
            sparse_entries=entries,
        )

        result = prepare_df_for_moving_average(df)
        miner2_at_t1 = result[
            (result["miner_id"] == 2) & (result["scored_time"] == times[1])
        ]
        assert len(miner2_at_t1) == 1
        # Should keep the real score, not the backfill value
        assert miner2_at_t1.iloc[0]["prompt_score_v3"] == pytest.approx(0.003)

    def test_none_percentile_skips_backfill(self):
        """If percentile95 or lowest_score is None, that time is skipped for backfill."""
        times = [_ts(0), _ts(60)]
        miner_ids = [1, 2]
        entries = [(0, 0), (0, 1), (1, 1)]
        df = _make_scores_df(
            miner_ids,
            times,
            assets=["BTC", "ETH"],
            scores=[0.005],
            sparse_entries=entries,
        )
        # Set percentile95 to None for t=0
        df.loc[df["scored_time"] == times[0], "percentile95"] = None

        result = prepare_df_for_moving_average(df)
        miner2_backfilled = result[
            (result["miner_id"] == 2) & (result["scored_time"] == times[0])
        ]
        # Should still have the row but with NaN score (no backfill source)
        if len(miner2_backfilled) > 0:
            assert pd.isna(miner2_backfilled.iloc[0]["prompt_score_v3"])

    def test_many_miners_many_times(self):
        """Stress test: 50 miners × 100 times, all present."""
        miners = list(range(50))
        times = [_ts(i * 10) for i in range(100)]
        df = _make_scores_df(
            miners, times, assets=["BTC", "ETH", "SOL", "XAU"]
        )
        result = prepare_df_for_moving_average(df)
        assert len(result) == 50 * 100

    def test_new_miner_backfill_asset_matches(self):
        """Backfilled rows for new miners should have the correct asset for that time."""
        times = [_ts(0), _ts(60), _ts(120)]
        assets = ["BTC", "ETH", "SOL"]
        entries = [(0, 0), (0, 1), (0, 2), (1, 2)]
        df = _make_scores_df(
            [1, 2],
            times,
            assets=assets,
            scores=[0.005],
            sparse_entries=entries,
        )

        result = prepare_df_for_moving_average(df)
        miner2 = result[result["miner_id"] == 2].sort_values("scored_time")

        # Backfilled rows should have the asset from their scored_time
        backfilled_t0 = miner2[miner2["scored_time"] == times[0]]
        if len(backfilled_t0) > 0:
            assert backfilled_t0.iloc[0]["asset"] == "BTC"

        backfilled_t1 = miner2[miner2["scored_time"] == times[1]]
        if len(backfilled_t1) > 0:
            assert backfilled_t1.iloc[0]["asset"] == "ETH"


# ===========================================================================
# Tests for compute_smoothed_score
# ===========================================================================


class TestComputeSmoothedScore:
    """Tests for compute_smoothed_score."""

    def test_basic_single_miner(self):
        """Single miner with scores returns a valid reward."""
        times = [_ts(0), _ts(60)]
        df = _make_scores_df([1], times, assets=["BTC", "BTC"], scores=[0.005])
        df = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10})
        scored_time = _ts(120)

        result = compute_smoothed_score(handler, df, scored_time, CRYPTO_24H)

        assert result is not None
        assert len(result) == 1
        assert result[0]["miner_id"] == 1
        assert result[0]["miner_uid"] == 10
        assert "smoothed_score" in result[0]
        assert "reward_weight" in result[0]
        assert result[0]["prompt_name"] == "Crypto 24h"

    def test_multiple_miners_rewards_sum_to_coefficient(self):
        """Reward weights across all miners should sum to smoothed_score_coefficient."""
        times = [_ts(0), _ts(60)]
        df = _make_scores_df([1, 2, 3], times, assets=["BTC", "BTC"])
        df = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: 11, 3: 12})
        scored_time = _ts(120)

        result = compute_smoothed_score(handler, df, scored_time, CRYPTO_24H)

        assert result is not None
        total_weight = sum(r["reward_weight"] for r in result)
        assert total_weight == pytest.approx(
            SMOOTHED_SCORE_COEFFICIENT, abs=1e-6
        )

    def test_empty_df_returns_none(self):
        """Empty input returns None."""
        df = pd.DataFrame(
            columns=["scored_time", "miner_id", "prompt_score_v3", "asset"]
        )
        handler = _mock_handler({})
        result = compute_smoothed_score(handler, df, _ts(0), CRYPTO_24H)
        assert result is None

    def test_scored_time_filters_future(self):
        """Only scores at or before scored_time should be included."""
        times = [_ts(0), _ts(60), _ts(120)]
        # Two miners so normalization doesn't collapse scores,
        # and varying scores per time so the sum changes with more data
        rows = []
        score_vals = [0.001, 0.005, 0.009]
        for ti, t in enumerate(times):
            rows.append(
                {
                    "miner_id": 1,
                    "prompt_score_v3": score_vals[ti],
                    "scored_time": t,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            )
            rows.append(
                {
                    "miner_id": 2,
                    "prompt_score_v3": 0.003,
                    "scored_time": t,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)

        handler = _mock_handler({1: 10, 2: 11})
        prepared = prepare_df_for_moving_average(df)

        # scored_time before the last entry — includes only t=0 and t=60
        result_partial = compute_smoothed_score(
            handler, prepared, _ts(60), CRYPTO_24H
        )
        # scored_time includes all three
        result_full = compute_smoothed_score(
            handler, prepared, _ts(120), CRYPTO_24H
        )

        assert result_partial is not None and result_full is not None

        partial_m1 = next(r for r in result_partial if r["miner_id"] == 1)
        full_m1 = next(r for r in result_full if r["miner_id"] == 1)
        # More data points with different scores should change the smoothed score
        assert partial_m1["smoothed_score"] != full_m1["smoothed_score"]

    def test_miner_with_no_uid_filtered_out(self):
        """Miners where populate_miner_uid returns None should be excluded."""
        times = [_ts(0)]
        df = _make_scores_df([1, 2], times, assets=["BTC"])
        df = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: None})  # miner 2 has no uid

        result = compute_smoothed_score(handler, df, _ts(60), CRYPTO_24H)

        assert result is not None
        miner_ids = [r["miner_id"] for r in result]
        assert 2 not in miner_ids

    def test_high_frequency_config(self):
        """Should work with CRYPTO_1H config and set correct prompt_name."""
        times = [_ts(0), _ts(10)]
        df = _make_scores_df([1], times, assets=["BTC", "BTC"], scores=[0.005])
        df = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10})
        result = compute_smoothed_score(handler, df, _ts(60), CRYPTO_1H)

        assert result is not None
        assert result[0]["prompt_name"] == "Crypto 1h"

    def test_com_equ_competition_config(self):
        """COM_EQU_24H sets its label and scores its assets, including the
        SPCX feed added in the split (must have an ASSET_COEFFICIENTS entry).
        """
        times = [_ts(0), _ts(60)]
        df = _make_scores_df(
            [1], times, assets=["SPCX", "XAU"], scores=[0.005]
        )
        df = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10})
        result = compute_smoothed_score(handler, df, _ts(120), COM_EQU_24H)

        assert result is not None
        assert result[0]["prompt_name"] == "Commodities/Equities 24h"

    def test_better_miner_gets_more_weight(self):
        """Miner with lower scores (better) should get higher reward weight."""
        times = [_ts(0), _ts(60)]
        rows = []
        for t in times:
            rows.append(
                {
                    "miner_id": 1,
                    "prompt_score_v3": 0.001,
                    "scored_time": t,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            )
            rows.append(
                {
                    "miner_id": 2,
                    "prompt_score_v3": 0.009,
                    "scored_time": t,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
        df = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: 11})
        result = compute_smoothed_score(handler, df, _ts(120), CRYPTO_24H)

        assert result is not None
        rewards = {r["miner_id"]: r["reward_weight"] for r in result}
        # Lower score = better = higher weight (softmax_beta is negative)
        assert rewards[1] > rewards[2]

    def test_result_has_updated_at(self):
        """Each result should have an ISO-format updated_at matching scored_time."""
        times = [_ts(0)]
        df = _make_scores_df([1], times, assets=["BTC"], scores=[0.005])
        df = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10})
        scored_time = _ts(60)
        result = compute_smoothed_score(handler, df, scored_time, CRYPTO_24H)

        assert result is not None
        assert result[0]["updated_at"] == scored_time.isoformat()

    def test_nan_scores_dropped(self):
        """Miners with all NaN prompt_score_v3 produce no rewards."""
        t = _ts(0)
        df = pd.DataFrame(
            {
                "miner_id": [1],
                "prompt_score_v3": [float("nan")],
                "scored_time": [t],
                "asset": ["BTC"],
            }
        )
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)

        handler = _mock_handler({1: 10})
        result = compute_smoothed_score(handler, df, _ts(60), CRYPTO_24H)

        # All scores are NaN -> no valid data -> None or empty
        assert result is None or len(result) == 0

    def test_miner_with_all_nan_among_valid_miners(self):
        """A miner with only NaN scores among valid miners gets inf rolling avg and is filtered out."""
        t = _ts(0)
        df = pd.DataFrame(
            {
                "miner_id": [1, 1, 2, 2],
                "prompt_score_v3": [0.005, 0.003, float("nan"), float("nan")],
                "scored_time": [t, _ts(60), t, _ts(60)],
                "asset": ["BTC", "BTC", "BTC", "BTC"],
            }
        )
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)

        handler = _mock_handler({1: 10, 2: 11})
        result = compute_smoothed_score(handler, df, _ts(120), CRYPTO_24H)

        assert result is not None
        # Miner 2 has inf rolling avg -> zero softmax weight -> filtered out
        miner_ids = [r["miner_id"] for r in result]
        assert 1 in miner_ids
        assert 2 not in miner_ids

    def test_multiple_assets_weighted(self):
        """Scores from different assets should be weighted by their coefficients."""
        t = _ts(0)
        rows = [
            {
                "miner_id": 1,
                "prompt_score_v3": 0.01,
                "scored_time": t,
                "asset": "BTC",
                "percentile95": 0.02,
                "lowest_score": 0.0,
            },
            {
                "miner_id": 1,
                "prompt_score_v3": 0.01,
                "scored_time": t,
                "asset": "XAU",
                "percentile95": 0.02,
                "lowest_score": 0.0,
            },
        ]
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)

        handler = _mock_handler({1: 10})
        result = compute_smoothed_score(handler, df, _ts(60), CRYPTO_24H)

        assert result is not None
        # XAU has higher coefficient than BTC, so weighted sum != simple sum
        assert result[0]["smoothed_score"] != pytest.approx(0.02)


# ===========================================================================
# Tests for combine_moving_averages
# ===========================================================================


class TestNumericalEquivalence:
    """Verify the vectorized compute_smoothed_score produces identical results
    to the original per-miner loop implementation."""

    def _old_compute_smoothed_score(
        self, input_df, scored_time, prompt_config
    ):
        """Reference implementation: the original per-miner loop."""
        if input_df.empty:
            return {}
        grouped = input_df.groupby("miner_id")
        result = {}
        for miner_id, group_df in grouped:
            group_df = group_df.copy()
            group_df["scored_time"] = pd.to_datetime(group_df["scored_time"])
            group_df = group_df.sort_values("scored_time")
            mask = group_df["scored_time"] <= scored_time
            window_df = group_df.loc[mask]
            valid_scores = window_df[["prompt_score_v3", "asset"]].dropna()
            if valid_scores.empty:
                result[miner_id] = float("inf")
                continue
            coefs = valid_scores["asset"].map(ASSET_COEFFICIENTS).fillna(1.0)
            weighted = valid_scores["prompt_score_v3"] * coefs / coefs.sum()
            result[miner_id] = float(weighted.sum())
        return result

    def test_single_asset_equivalence(self):
        """Single asset: new and old produce identical smoothed scores."""
        times = [_ts(0), _ts(60), _ts(120)]
        rows = []
        for t in times:
            for mid in [1, 2, 3]:
                rows.append(
                    {
                        "miner_id": mid,
                        "prompt_score_v3": 0.001 * mid,
                        "scored_time": t,
                        "asset": "BTC",
                        "percentile95": 0.01,
                        "lowest_score": 0.0,
                    }
                )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
        prepared = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: 11, 3: 12})
        scored_time = _ts(180)

        new_result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_24H
        )
        old_result = self._old_compute_smoothed_score(
            prepared, scored_time, CRYPTO_24H
        )

        assert new_result is not None
        for r in new_result:
            assert r["smoothed_score"] == pytest.approx(
                old_result[r["miner_id"]], rel=1e-10
            )

    def test_multi_asset_equivalence(self):
        """Multiple assets with different coefficients: new and old match."""
        assets = ["BTC", "ETH", "XAU", "SOL"]
        times = [_ts(i * 60) for i in range(len(assets))]
        rows = []
        for ti, t in enumerate(times):
            for mid in [1, 2, 3]:
                rows.append(
                    {
                        "miner_id": mid,
                        "prompt_score_v3": 0.002 * mid + 0.001 * ti,
                        "scored_time": t,
                        "asset": assets[ti],
                        "percentile95": 0.02,
                        "lowest_score": 0.0,
                    }
                )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
        prepared = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: 11, 3: 12})
        scored_time = _ts(300)

        new_result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_24H
        )
        old_result = self._old_compute_smoothed_score(
            prepared, scored_time, CRYPTO_24H
        )

        assert new_result is not None
        for r in new_result:
            assert r["smoothed_score"] == pytest.approx(
                old_result[r["miner_id"]], rel=1e-10
            )

    def test_realistic_equivalence(self):
        """Realistic data (248 miners, 12 assets): new and old match."""
        rng = np.random.RandomState(42)
        assets = [
            "BTC",
            "ETH",
            "XAU",
            "SOL",
            "SPYX",
            "NVDAX",
            "TSLAX",
            "AAPLX",
            "GOOGLX",
            "XRP",
            "HYPE",
            "WTIOIL",
        ]
        n_miners, n_times = 50, 24
        times = [_ts(i * 65) for i in range(n_times)]
        rows = []
        for ti, t in enumerate(times):
            asset = assets[ti % len(assets)]
            for mid in range(n_miners):
                score = 0.0 if rng.random() < 0.12 else rng.exponential(0.004)
                rows.append(
                    {
                        "miner_id": mid,
                        "prompt_score_v3": min(score, 0.15),
                        "scored_time": t,
                        "asset": asset,
                        "percentile95": 0.01,
                        "lowest_score": 0.0,
                    }
                )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
        prepared = prepare_df_for_moving_average(df)

        handler = _mock_handler({mid: mid for mid in range(n_miners)})
        scored_time = _ts(n_times * 65 + 60)

        new_result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_24H
        )
        old_result = self._old_compute_smoothed_score(
            prepared, scored_time, CRYPTO_24H
        )

        assert new_result is not None
        for r in new_result:
            assert r["smoothed_score"] == pytest.approx(
                old_result[r["miner_id"]], rel=1e-10
            ), f"Miner {r['miner_id']}: new={r['smoothed_score']}, old={old_result[r['miner_id']]}"


class TestCombineMovingAverages:
    """Tests for combine_moving_averages."""

    def test_single_prompt(self):
        """Single prompt type: output == input."""
        data = {
            "low": [
                {"miner_id": 1, "miner_uid": 10, "reward_weight": 0.3},
                {"miner_id": 2, "miner_uid": 11, "reward_weight": 0.2},
            ]
        }
        result = combine_moving_averages(data)
        assert len(result) == 2
        weights = {r["miner_id"]: r["reward_weight"] for r in result}
        assert weights[1] == pytest.approx(0.3)
        assert weights[2] == pytest.approx(0.2)

    def test_two_prompts_same_miner(self):
        """Same miner in both low and high: weights should be summed."""
        data = {
            "low": [{"miner_id": 1, "miner_uid": 10, "reward_weight": 0.3}],
            "high": [{"miner_id": 1, "miner_uid": 10, "reward_weight": 0.2}],
        }
        result = combine_moving_averages(data)
        assert len(result) == 1
        assert result[0]["reward_weight"] == pytest.approx(0.5)

    def test_two_prompts_different_miners(self):
        """Different miners in low/high: both appear in output."""
        data = {
            "low": [{"miner_id": 1, "miner_uid": 10, "reward_weight": 0.3}],
            "high": [{"miner_id": 2, "miner_uid": 11, "reward_weight": 0.2}],
        }
        result = combine_moving_averages(data)
        assert len(result) == 2

    def test_empty_input(self):
        """Empty dict returns empty list."""
        result = combine_moving_averages({})
        assert result == []

    def test_empty_lists(self):
        """Dict with empty lists returns empty list."""
        result = combine_moving_averages({"low": [], "high": []})
        assert result == []

    def test_preserves_other_fields(self):
        """Non-weight fields from the first occurrence are preserved."""
        data = {
            "low": [
                {
                    "miner_id": 1,
                    "miner_uid": 10,
                    "reward_weight": 0.3,
                    "smoothed_score": 0.01,
                    "updated_at": "2025-06-01",
                    "prompt_name": "low",
                }
            ],
            "high": [
                {
                    "miner_id": 1,
                    "miner_uid": 10,
                    "reward_weight": 0.2,
                    "smoothed_score": 0.02,
                    "updated_at": "2025-06-01",
                    "prompt_name": "high",
                }
            ],
        }
        result = combine_moving_averages(data)
        assert result[0]["miner_uid"] == 10
        # smoothed_score comes from first occurrence (low)
        assert result[0]["smoothed_score"] == 0.01

    def test_many_miners(self):
        """Handles many miners correctly."""
        low = [
            {"miner_id": i, "miner_uid": i, "reward_weight": 0.01}
            for i in range(100)
        ]
        high = [
            {"miner_id": i, "miner_uid": i, "reward_weight": 0.02}
            for i in range(100)
        ]
        result = combine_moving_averages({"low": low, "high": high})
        assert len(result) == 100
        for r in result:
            assert r["reward_weight"] == pytest.approx(0.03)

    def test_three_competitions_combined_sum_to_one(self):
        """The 2->3 split invariant: each competition's reward_weights sum to
        SMOOTHED_SCORE_COEFFICIENT (1/3), so combining the three real labels
        yields a total of ~1.0. Crypto miners (in both Crypto 1h and Crypto
        24h) get their two weights summed; a commodity miner appears once.
        """
        third = SMOOTHED_SCORE_COEFFICIENT
        data = {
            "Crypto 1h": [
                {"miner_id": 1, "miner_uid": 1, "reward_weight": third / 2},
                {"miner_id": 2, "miner_uid": 2, "reward_weight": third / 2},
            ],
            "Crypto 24h": [
                {"miner_id": 1, "miner_uid": 1, "reward_weight": third / 2},
                {"miner_id": 2, "miner_uid": 2, "reward_weight": third / 2},
            ],
            "Commodities/Equities 24h": [
                {"miner_id": 3, "miner_uid": 3, "reward_weight": third},
            ],
        }
        result = combine_moving_averages(data)

        total = sum(r["reward_weight"] for r in result)
        assert total == pytest.approx(1.0, abs=1e-9)

        weights = {r["miner_id"]: r["reward_weight"] for r in result}
        # crypto miners scored in both crypto competitions -> summed
        assert weights[1] == pytest.approx(third)
        assert weights[2] == pytest.approx(third)
        # commodity/equity miner appears only in its one competition
        assert weights[3] == pytest.approx(third)
        assert len(result) == 3


# ===========================================================================
# Integration-style tests (prepare -> compute -> combine)
# ===========================================================================


class TestRealisticData:
    """Tests using data shaped like real production data."""

    def _make_realistic_df(
        self, n_miners=248, n_times=90, assets=None, mode="low"
    ):
        """Build a DataFrame mimicking real get_miner_scores output.

        Real data: ~248 miners, ~90 times over window_days,
        scores in [0, 0.15] with mean ~0.004, ~12% zeros, 99.5% fill rate.
        Low-freq: 12 assets, ~1h apart. HFT: 5 assets, ~10min apart.
        """
        if assets is None:
            if mode == "low":
                assets = [
                    "BTC",
                    "ETH",
                    "XAU",
                    "SOL",
                    "SPYX",
                    "NVDAX",
                    "TSLAX",
                    "AAPLX",
                    "GOOGLX",
                    "XRP",
                    "HYPE",
                    "WTIOIL",
                ]
            else:
                assets = ["BTC", "ETH", "XAU", "SOL", "HYPE"]
        rng = np.random.RandomState(42)
        interval = 65 if mode == "low" else 10  # ~1h for LF, ~10min for HFT
        times = [_ts(i * interval) for i in range(n_times)]
        rows = []
        for ti, t in enumerate(times):
            asset = assets[ti % len(assets)]
            p95 = rng.uniform(0.008, 0.015)
            low = 0.0
            for mi in range(n_miners):
                # ~0.5% chance of missing (99.5% fill rate)
                if rng.random() < 0.005:
                    continue
                # ~12% chance of score=0 (best miner)
                if rng.random() < 0.12:
                    score = 0.0
                else:
                    score = rng.exponential(0.004)
                    score = min(score, 0.15)  # cap like real data
                rows.append(
                    {
                        "miner_id": mi,
                        "prompt_score_v3": score,
                        "scored_time": t,
                        "asset": asset,
                        "percentile95": p95,
                        "lowest_score": low,
                    }
                )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
        return df

    def test_prepare_realistic_shape(self):
        """prepare_df_for_moving_average on realistic data returns correct shape."""
        df = self._make_realistic_df()
        result = prepare_df_for_moving_average(df)
        # Should have roughly same number of rows (few new miners to backfill)
        assert len(result) >= len(df) * 0.99
        assert set(result.columns) == {
            "scored_time",
            "miner_id",
            "prompt_score_v3",
            "asset",
        }

    def test_compute_smoothed_realistic(self):
        """Full pipeline on realistic data produces valid rewards."""
        df = self._make_realistic_df()
        prepared = prepare_df_for_moving_average(df)

        # Mock all miners having UIDs
        miner_ids = sorted(df["miner_id"].unique())
        handler = _mock_handler({mid: mid for mid in miner_ids})

        scored_time = df["scored_time"].max() + timedelta(hours=1)
        result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_24H
        )

        assert result is not None
        assert len(result) > 0

        # Reward weights should sum to smoothed_score_coefficient
        total_weight = sum(r["reward_weight"] for r in result)
        assert total_weight == pytest.approx(
            SMOOTHED_SCORE_COEFFICIENT, abs=1e-4
        )

        # All smoothed scores should be non-negative
        smoothed_scores = [r["smoothed_score"] for r in result]
        assert min(smoothed_scores) >= 0

    def test_zero_scores_handled(self):
        """Miners with score=0 (best) should get lowest smoothed score and highest weight."""
        times = [_ts(0), _ts(60)]
        rows = []
        for t in times:
            # Miner 1: perfect score (0)
            rows.append(
                {
                    "miner_id": 1,
                    "prompt_score_v3": 0.0,
                    "scored_time": t,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            )
            # Miner 2: average score
            rows.append(
                {
                    "miner_id": 2,
                    "prompt_score_v3": 0.005,
                    "scored_time": t,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            )
            # Miner 3: worst score
            rows.append(
                {
                    "miner_id": 3,
                    "prompt_score_v3": 0.01,
                    "scored_time": t,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)
        prepared = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: 11, 3: 12})
        result = compute_smoothed_score(
            handler, prepared, _ts(120), CRYPTO_24H
        )

        assert result is not None
        rewards = {r["miner_id"]: r for r in result}

        # Score=0 miner should have lowest smoothed_score
        assert rewards[1]["smoothed_score"] < rewards[2]["smoothed_score"]
        assert rewards[2]["smoothed_score"] < rewards[3]["smoothed_score"]
        # And highest reward weight (softmax_beta is negative)
        assert rewards[1]["reward_weight"] > rewards[2]["reward_weight"]
        assert rewards[2]["reward_weight"] > rewards[3]["reward_weight"]

    def test_compute_smoothed_realistic_hft(self):
        """Full pipeline on realistic HFT data (5 assets, 3-day window)."""
        df = self._make_realistic_df(
            n_miners=248, n_times=432, mode="high"
        )  # ~3 days at 10min
        prepared = prepare_df_for_moving_average(df)

        miner_ids = sorted(df["miner_id"].unique())
        handler = _mock_handler({mid: mid for mid in miner_ids})

        scored_time = df["scored_time"].max() + timedelta(hours=1)
        result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_1H
        )

        assert result is not None
        assert len(result) > 0
        total_weight = sum(r["reward_weight"] for r in result)
        assert total_weight == pytest.approx(
            SMOOTHED_SCORE_COEFFICIENT, abs=1e-4
        )

    def test_asset_coefficient_weighting(self):
        """Different assets have different coefficients — verify correct weighting."""
        # Use 3 assets with distinct coefficients
        assets_to_test = ["BTC", "XAU", "SOL"]
        rows = []
        for i, asset in enumerate(assets_to_test):
            rows.append(
                {
                    "miner_id": 1,
                    "prompt_score_v3": 0.01,
                    "scored_time": _ts(i * 60),
                    "asset": asset,
                    "percentile95": 0.02,
                    "lowest_score": 0.0,
                }
            )
        df = pd.DataFrame(rows)
        df["scored_time"] = pd.to_datetime(df["scored_time"], utc=True)

        handler = _mock_handler({1: 10})
        prepared = prepare_df_for_moving_average(df)
        result = compute_smoothed_score(
            handler, prepared, _ts(180), CRYPTO_24H
        )

        assert result is not None
        coefs = [ASSET_COEFFICIENTS[a] for a in assets_to_test]
        expected = sum(0.01 * c for c in coefs) / sum(coefs)
        assert result[0]["smoothed_score"] == pytest.approx(expected, rel=1e-6)

    def test_realistic_with_new_miner(self):
        """New miner appearing at last time in realistic data gets backfilled."""
        df = self._make_realistic_df(n_miners=50, n_times=10)

        # Add a brand new miner only at the last time
        last_time = df["scored_time"].max()
        new_row = pd.DataFrame(
            [
                {
                    "miner_id": 999,
                    "prompt_score_v3": 0.003,
                    "scored_time": last_time,
                    "asset": "BTC",
                    "percentile95": 0.01,
                    "lowest_score": 0.0,
                }
            ]
        )
        new_row["scored_time"] = pd.to_datetime(
            new_row["scored_time"], utc=True
        )
        df = pd.concat([df, new_row], ignore_index=True)

        prepared = prepare_df_for_moving_average(df)

        # New miner should have backfilled rows at earlier times
        miner999 = prepared[prepared["miner_id"] == 999]
        assert len(miner999) > 1  # more than just the real row

        # Backfilled scores should be worst score (p95 - lowest)
        backfilled = miner999[miner999["scored_time"] < last_time]
        assert len(backfilled) > 0
        for _, row in backfilled.iterrows():
            assert row["prompt_score_v3"] > 0  # worst score, not zero

    def test_no_new_miners_fast_path(self):
        """When all miners present from the start, no cartesian product needed."""
        times = [_ts(0), _ts(60), _ts(120)]
        # All 5 miners present at all times
        df = _make_scores_df(
            list(range(5)),
            times,
            assets=["BTC", "SOL", "BTC"],
            scores=[0.0, 0.001, 0.003, 0.005, 0.01],
        )
        result = prepare_df_for_moving_average(df)
        # No backfill needed — exact same row count
        assert len(result) == len(df)

    def test_existing_test_csv_compatible(self):
        """Run prepare + compute on the real cutoff_data_4_days.csv shape."""
        # Simulate the CSV shape: no percentile95/lowest_score columns
        # (they get added by get_miner_scores after the merge)
        import os

        csv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "cutoff_data_4_days.csv",
        )
        if not os.path.exists(csv_path):
            pytest.skip("cutoff_data_4_days.csv not found")

        df = pd.read_csv(csv_path)
        df["scored_time"] = pd.to_datetime(df["scored_time"])

        # The real CSV doesn't have percentile95/lowest_score — add them
        # to simulate what get_miner_scores returns
        df["percentile95"] = 0.01
        df["lowest_score"] = 0.0

        prepared = prepare_df_for_moving_average(df)

        assert len(prepared) >= len(df)
        assert not prepared["prompt_score_v3"].isna().all()

        # Run compute_smoothed_score on the prepared data
        miner_ids = sorted(df["miner_id"].unique())
        handler = _mock_handler({mid: mid for mid in miner_ids})
        scored_time = df["scored_time"].max() + timedelta(hours=1)

        result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_24H
        )
        assert result is not None
        assert len(result) > 200  # ~248 miners expected


class TestEndToEnd:
    """End-to-end tests running the full pipeline without DB."""

    def test_full_pipeline_basic(self):
        """Run the full pipeline: prepare -> compute (LF+HFT) -> combine."""
        times = [_ts(0), _ts(60), _ts(120)]
        miners = [1, 2, 3]
        df = _make_scores_df(miners, times, assets=["BTC", "ETH", "SOL"])
        prepared = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: 11, 3: 12})
        scored_time = _ts(180)

        low_result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_24H
        )
        high_result = compute_smoothed_score(
            handler, prepared, scored_time, CRYPTO_1H
        )

        assert low_result is not None
        assert high_result is not None

        combined = combine_moving_averages(
            {"low": low_result, "high": high_result}
        )
        assert len(combined) == 3

        # All miners should have combined weights
        for r in combined:
            assert r["reward_weight"] > 0

    def test_full_pipeline_with_new_miner(self):
        """New miner joining late gets backfilled, scored, and combined."""
        times = [_ts(0), _ts(60), _ts(120)]
        entries = [
            (0, 0),
            (0, 1),
            (0, 2),  # miner 1 at all times
            (1, 0),
            (1, 1),
            (1, 2),  # miner 2 at all times
            (2, 2),  # miner 3 joins at t=120
        ]
        df = _make_scores_df(
            [1, 2, 3],
            times,
            assets=["BTC", "ETH", "SOL"],
            scores=[0.005],
            sparse_entries=entries,
        )
        prepared = prepare_df_for_moving_average(df)

        handler = _mock_handler({1: 10, 2: 11, 3: 12})
        result = compute_smoothed_score(
            handler, prepared, _ts(180), CRYPTO_24H
        )

        assert result is not None
        assert len(result) == 3
        # All 3 miners should have rewards
        miner_ids = {r["miner_id"] for r in result}
        assert miner_ids == {1, 2, 3}

    def test_full_pipeline_deterministic(self):
        """Running the same inputs twice gives identical results."""
        times = [_ts(0), _ts(60)]
        df = _make_scores_df(
            [1, 2], times, assets=["BTC", "ETH"], scores=[0.003, 0.005]
        )

        handler = _mock_handler({1: 10, 2: 11})
        scored_time = _ts(120)

        prepared1 = prepare_df_for_moving_average(df)
        result1 = compute_smoothed_score(
            handler, prepared1, scored_time, CRYPTO_24H
        )

        prepared2 = prepare_df_for_moving_average(df)
        result2 = compute_smoothed_score(
            handler, prepared2, scored_time, CRYPTO_24H
        )

        assert result1 is not None and result2 is not None
        for r1, r2 in zip(
            sorted(result1, key=lambda x: x["miner_id"]),
            sorted(result2, key=lambda x: x["miner_id"]),
        ):
            assert r1["smoothed_score"] == pytest.approx(r2["smoothed_score"])
            assert r1["reward_weight"] == pytest.approx(r2["reward_weight"])

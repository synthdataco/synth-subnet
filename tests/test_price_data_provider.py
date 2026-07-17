from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch
import numpy as np


from synth.db.models import ValidatorRequest
from synth.validator.price_data_provider import PriceDataProvider

validator_request = ValidatorRequest(
    asset="BTC",
    start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
    time_length=360,
    time_increment=120,
)


def _hl_candles(timestamps: list[int], closes: list[float]) -> list[dict]:
    """Hyperliquid candleSnapshot shape: open time in ms, close as string."""
    return [{"t": t * 1000, "c": str(c)} for t, c in zip(timestamps, closes)]


def _mock_hl_session(candles: list[dict]) -> MagicMock:
    """A stand-in for requests.Session whose post() returns `candles`."""
    session_cls = MagicMock()
    session = session_cls.return_value.__enter__.return_value
    session.post.return_value.json.return_value = candles
    return session_cls


class TestPriceDataProvider(unittest.TestCase):
    def setUp(self):
        self.dataProvider = PriceDataProvider()

    def test_fetch_data_all_prices(self):
        # 1739974320 - 2025-02-19T14:12:00+00:00
        # 1739974380 - 2025-02-19T14:13:00+00:00
        # 1739974440 - 2025-02-19T14:14:00+00:00
        # 1739974500 - 2025-02-19T14:15:00+00:00
        # 1739974560 - 2025-02-19T14:16:00+00:00
        # 1739974620 - 2025-02-19T14:17:00+00:00
        # 1739974680 - 2025-02-19T14:18:00+00:00 (last grid point)
        # 1739974740 - 2025-02-19T14:19:00+00:00 (settlement witness)
        candles = _hl_candles(
            [
                1739974320,
                1739974380,
                1739974440,
                1739974500,
                1739974560,
                1739974620,
                1739974680,
                1739974740,
            ],
            [
                100000.23,
                101000.55,
                99000.55,
                102000.55,
                103000.55,
                105000.55,
                108000.867,
                108500.0,
            ],
        )

        with patch("requests.Session", _mock_hl_session(candles)):
            result = self.dataProvider.fetch_data(validator_request)

            assert result == [100000.23, 99000.55, 103000.55, 108000.867]

    def test_fetch_data_gap_1(self):
        # 1739974320 - 2025-02-19T14:12:00+00:00
        # gap        - 2025-02-19T14:13:00+00:00
        # gap        - 2025-02-19T14:14:00+00:00
        # gap        - 2025-02-19T14:15:00+00:00
        # gap        - 2025-02-19T14:16:00+00:00
        # 1739974620 - 2025-02-19T14:17:00+00:00
        # 1739974680 - 2025-02-19T14:18:00+00:00
        # 1739974740 (14:19) is the settlement-witness candle proving
        # the last grid point's 1-min candle has closed.
        candles = _hl_candles(
            [1739974320, 1739974620, 1739974680, 1739974740],
            [100000.23, 105000.55, 108000.867, 108500.0],
        )

        with patch("requests.Session", _mock_hl_session(candles)):
            result = self.dataProvider.fetch_data(validator_request)

            assert result == [100000.23, np.nan, np.nan, 108000.867]

    def test_fetch_data_gap_2(self):
        # 1739974320 - 2025-02-19T14:12:00+00:00
        # gap        - 2025-02-19T14:13:00+00:00
        # gap        - 2025-02-19T14:14:00+00:00
        # gap        - 2025-02-19T14:15:00+00:00
        # gap        - 2025-02-19T14:16:00+00:00
        # gap        - 2025-02-19T14:17:00+00:00
        # 1739974680 - 2025-02-19T14:18:00+00:00
        # 1739974740 is the settlement-witness candle.
        candles = _hl_candles(
            [1739974320, 1739974680, 1739974740],
            [100000.23, 108000.867, 108500.0],
        )

        with patch("requests.Session", _mock_hl_session(candles)):
            validator_request_eth = ValidatorRequest(
                asset="ETH",
                start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
                time_length=360,
                time_increment=60,
            )
            result = self.dataProvider.fetch_data(validator_request_eth)

            assert result == [
                100000.23,
                np.nan,
                np.nan,
                np.nan,
                np.nan,
                np.nan,
                108000.867,
            ]

    def test_transform_data_non_divisible_grid(self):
        # time_length=540 with time_increment=120 produces one extra grid
        # point past start + time_length (the precaution branch in
        # _transform_data).
        data = {
            "t": [
                1739974320,
                1739974680,
                1739974740,
                1739974800,
                1739974860,
                1739974920,
            ],
            "c": [
                100000.23,
                108000.867,
                99000.23,
                97123.55,
                105123.345,
                107995.889,
            ],
        }

        result = PriceDataProvider._transform_data(data, 1739974320, 120, 540)

        assert result == [
            100000.23,
            np.nan,
            np.nan,
            108000.867,
            97123.55,
            107995.889,
        ]

    def test_fetch_data_gap_from_start(self):
        # gap        - 2025-02-19T14:12:00+00:00
        # gap        - 2025-02-19T14:13:00+00:00
        # gap        - 2025-02-19T14:14:00+00:00
        # gap        - 2025-02-19T14:15:00+00:00
        # gap        - 2025-02-19T14:16:00+00:00
        # gap        - 2025-02-19T14:17:00+00:00
        # 1739974680 - 2025-02-19T14:18:00+00:00
        # 1739974740 - 2025-02-19T14:19:00+00:00
        # 1739974800 - 2025-02-19T14:20:00+00:00
        # 1739974860 - 2025-02-19T14:21:00+00:00
        # 1739974920 - 2025-02-19T14:22:00+00:00
        candles = _hl_candles(
            [1739974680, 1739974740, 1739974800, 1739974860, 1739974920],
            [108000.867, 99000.23, 97123.55, 105123.345, 107995.889],
        )

        with patch("requests.Session", _mock_hl_session(candles)):
            result = self.dataProvider.fetch_data(validator_request)

            assert result == [np.nan, np.nan, np.nan, 108000.867]

    def test_fetch_data_gap_from_start_2(self):
        # gap        - 2025-02-19T14:12:00+00:00
        # 1739974380 - 2025-02-19T14:13:00+00:00
        # 1739974440 - 2025-02-19T14:14:00+00:00
        # 1739974500 - 2025-02-19T14:15:00+00:00
        # 1739974560 - 2025-02-19T14:16:00+00:00
        # 1739974620 - 2025-02-19T14:17:00+00:00
        # 1739974680 - 2025-02-19T14:18:00+00:00
        # 1739974740 - 2025-02-19T14:19:00+00:00
        # 1739974800 - 2025-02-19T14:20:00+00:00
        # 1739974860 - 2025-02-19T14:21:00+00:00
        # 1739974920 - 2025-02-19T14:22:00+00:00
        # 1739974980 (14:23) is the settlement-witness candle for the
        # local request below whose last grid point is 14:22.
        candles = _hl_candles(
            [
                1739974380,
                1739974440,
                1739974500,
                1739974560,
                1739974620,
                1739974680,
                1739974740,
                1739974800,
                1739974860,
                1739974920,
                1739974980,
            ],
            [
                101000.55,
                99000.55,
                102000.55,
                103000.55,
                105000.55,
                108000.867,
                99000.23,
                97123.55,
                105123.345,
                107995.889,
                108500.0,
            ],
        )

        with patch("requests.Session", _mock_hl_session(candles)):
            validator_request = ValidatorRequest(
                asset="BTC",
                start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
                time_length=600,
                time_increment=300,
            )

            result = self.dataProvider.fetch_data(validator_request)

            assert result == [np.nan, 105000.55, 107995.889]

    def test_fetch_data_gap_in_the_middle(self):
        # 1739974320 - 2025-02-20T14:12:00+00:00
        # 1739974380 - 2025-02-20T14:13:00+00:00
        # 1739974440 - 2025-02-20T14:14:00+00:00
        # 1739974500 - 2025-02-20T14:15:00+00:00
        # 1739974560 - 2025-02-20T14:16:00+00:00
        # gap        - 2025-02-20T14:17:00+00:00
        # 1739974680 - 2025-02-20T14:18:00+00:00
        # 1739974740 - 2025-02-20T14:19:00+00:00
        # 1739974800 - 2025-02-20T14:20:00+00:00
        # 1739974860 - 2025-02-20T14:21:00+00:00
        # 1739974920 - 2025-02-20T14:22:00+00:00
        # 1739974980 - 2025-02-20T14:23:00+00:00
        candles = _hl_candles(
            [
                1739974320,
                1739974380,
                1739974440,
                1739974500,
                1739974560,
                1739974680,
                1739974740,
                1739974800,
                1739974860,
                1739974920,
                1739974980,
            ],
            [
                100000.23,
                101000.55,
                99000.55,
                102000.55,
                103000.55,
                108000.867,
                108000.867,
                99000.23,
                97123.55,
                105123.345,
                107995.889,
            ],
        )

        with patch("requests.Session", _mock_hl_session(candles)):
            validator_request = ValidatorRequest(
                asset="BTC",
                start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
                time_length=600,
                time_increment=300,
            )

            result = self.dataProvider.fetch_data(validator_request)

            assert result == [100000.23, np.nan, 105123.345]

    def test_fetch_data_several_values(self):
        # 1739974320 - 2025-02-20T14:12:00+00:00
        # 1739974380 - 2025-02-20T14:13:00+00:00
        # 1739974440 - 2025-02-20T14:14:00+00:00
        # 1739974500 - 2025-02-20T14:15:00+00:00
        # 1739974560 - 2025-02-20T14:16:00+00:00
        # 1739974620 - 2025-02-20T14:17:00+00:00
        # 1739974680 - 2025-02-20T14:18:00+00:00
        # 1739974740 - 2025-02-20T14:19:00+00:00
        # 1739974800 - 2025-02-20T14:20:00+00:00
        # 1739974860 - 2025-02-20T14:21:00+00:00
        # 1739974920 - 2025-02-20T14:22:00+00:00
        # 1739974980 - 2025-02-20T14:23:00+00:00
        candles = _hl_candles(
            [
                1739974320,
                1739974380,
                1739974440,
                1739974500,
                1739974560,
                1739974620,
                1739974680,
                1739974740,
                1739974800,
                1739974860,
                1739974920,
                1739974980,
            ],
            [
                100000.23,
                101000.55,
                99000.55,
                102000.55,
                103000.55,
                105000.55,
                108000.867,
                108000.867,
                99000.23,
                97123.55,
                105123.345,
                107995.889,
            ],
        )

        with patch("requests.Session", _mock_hl_session(candles)):
            validator_request = ValidatorRequest(
                asset="BTC",
                start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
                time_length=600,
                time_increment=300,
            )

            result = self.dataProvider.fetch_data(validator_request)

            assert result == [100000.23, 105000.55, 105123.345]

    def test_fetch_data(self):
        # Live call to Hyperliquid — uses a recent window because 1m
        # candles are only retained for ~3.5 days (rolling 5000 candles).
        # The shared module-level `validator_request` is fine for the
        # mocked tests above but its hardcoded 2025-02 date is outside
        # the live window.
        start = datetime.now(timezone.utc).replace(
            second=0, microsecond=0
        ) - timedelta(minutes=15)
        live_request = ValidatorRequest(
            asset="BTC",
            start_time=start,
            time_length=360,
            time_increment=120,
        )
        result = self.dataProvider.fetch_data(live_request)
        # 360s / 120s + 1 grid points; all finite and positive (BTC > 0).
        assert len(result) == 4
        assert all(np.isfinite(p) for p in result)
        assert all(p > 0 for p in result)


class TestSettlementGuard(unittest.TestCase):
    """The settlement guard refuses to return prices unless the response
    proves the last grid candle has closed (a candle with t strictly later
    than the last grid timestamp). Without it, scoring would consume an
    in-progress close that changes by the time we re-score, breaking CRPS
    reproducibility."""

    def test_hyperliquid_raises_when_no_candle_past_last_grid(self):
        # All candles are within the grid — no settlement witness.
        candles = _hl_candles(
            [1739974320, 1739974440, 1739974560, 1739974680],
            [1.0, 2.0, 3.0, 4.0],
        )

        provider = PriceDataProvider()
        # Call the unwrapped function to skip the tenacity retries (three
        # attempts with random exponential waits) around the raise.
        download = PriceDataProvider.download_hyperliquid_price_data
        with patch("requests.Session", _mock_hl_session(candles)):
            with self.assertRaises(ValueError):
                download.__wrapped__(
                    provider,
                    beginning=1739974320,
                    end=1739974680,
                    symbol="BTC",
                    time_increment=120,
                )

    def test_witness_accepted_when_present(self):
        # A candle past the last grid point proves settlement; the grid
        # itself transforms normally and the witness is discarded.
        candles = _hl_candles(
            [1739974320, 1739974680, 1739974740],
            [1.0, 4.0, 5.0],
        )

        provider = PriceDataProvider()
        download = PriceDataProvider.download_hyperliquid_price_data
        with patch("requests.Session", _mock_hl_session(candles)):
            result = download.__wrapped__(
                provider,
                beginning=1739974320,
                end=1739974680,
                symbol="BTC",
                time_increment=120,
            )

        assert result == [1.0, np.nan, np.nan, 4.0]

    def test_pyth_raises_when_no_candle_past_last_grid(self):
        data = {
            "t": [1739974320, 1739974440, 1739974560, 1739974680],
            "c": [1.0, 2.0, 3.0, 4.0],
        }
        with self.assertRaises(ValueError):
            PriceDataProvider._assert_settled(
                data, "SPYX", "req-1", last_grid_timestamp=1739974680
            )

    def test_pyth_accepts_when_witness_candle_present(self):
        data = {
            "t": [1739974320, 1739974680, 1739974740],
            "c": [1.0, 4.0, 5.0],
        }
        # Should not raise.
        PriceDataProvider._assert_settled(
            data, "SPYX", "req-1", last_grid_timestamp=1739974680
        )


class TestPriceDataProviderPythTail(unittest.TestCase):
    """SPYX rollout-tail coverage: retired from prompting but in-flight
    requests still score from Pyth Pro history until the tail ends."""

    def test_spyx_uses_pyth_pro_history(self):
        # 1739974740 is the settlement-witness candle past the last grid
        # point at 1739974680 (= start + time_length).
        mock_response = {
            "t": [
                1739974320,
                1739974440,
                1739974560,
                1739974680,
                1739974740,
            ],
            "c": [100000.23, 99000.55, 103000.55, 108000.867, 108500.0],
        }

        spyx_request = ValidatorRequest(
            asset="SPYX",
            start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
            time_length=360,
            time_increment=120,
        )

        provider = PriceDataProvider()
        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            result = provider.fetch_data(spyx_request)

            called_params = mock_get.call_args.kwargs["params"]
            assert (
                called_params["symbol"]
                == PriceDataProvider.PYTH_SYMBOL_MAP["SPYX"]
            )
            # The fetch window must extend one minute past the last grid
            # point so the settlement witness can land in the response.
            assert called_params["to"] == 1739974680 + 60
            assert result == [100000.23, 99000.55, 103000.55, 108000.867]


class TestPriceDataProviderLive(unittest.TestCase):
    """Hits the live Hyperliquid history endpoint for every asset — no
    mocks. The endpoint is public. Catches coin-code regressions per asset
    (e.g. a HIP-3 dex redeploy renaming a coin, as km:US500 -> mkts:US500
    did in July 2026)."""

    def _assert_live_history(self, provider, asset):
        end = datetime.now(timezone.utc).replace(
            second=0, microsecond=0
        ) - timedelta(minutes=5)
        start = end - timedelta(minutes=10)

        req = ValidatorRequest(
            asset=asset,
            start_time=start,
            time_length=600,
            time_increment=60,
        )
        prices = provider.fetch_data(req)

        # time_length=600s @ time_increment=60s => 11 grid points.
        self.assertEqual(len(prices), 11)
        finite = [p for p in prices if not np.isnan(p)]
        self.assertGreater(
            len(finite),
            5,
            f"{asset}: too many gaps: {prices}",
        )
        for p in finite:
            # Loose sanity bounds — XAU ~$5k, HYPE ~$40, BTC ~$80k.
            self.assertGreater(p, 0, f"{asset}: non-positive price")
            self.assertLess(p, 10_000_000, f"{asset}: suspicious magnitude")

    def test_live_history_per_asset(self):
        provider = PriceDataProvider()
        for asset in PriceDataProvider.HYPERLIQUID_ASSET_MAP.keys():
            with self.subTest(asset=asset):
                self._assert_live_history(provider, asset)

    def test_live_history_spyx_pyth_tail(self):
        # Rollout tail: SPYX still scores from Pyth Pro (keyless today).
        provider = PriceDataProvider()
        self._assert_live_history(provider, "SPYX")

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import numpy as np


from synth.db.models import ValidatorRequest
from synth.validator.price_data_provider import PriceDataProvider

validator_request = ValidatorRequest(
    asset="BTC",
    start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
    time_length=360,
    time_increment=120,
)


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
        mock_response = {
            "t": [
                1739974320,
                1739974380,
                1739974440,
                1739974500,
                1739974560,
                1739974620,
                1739974680,
                1739974740,
            ],
            "c": [
                100000.23,
                101000.55,
                99000.55,
                102000.55,
                103000.55,
                105000.55,
                108000.867,
                108500.0,
            ],
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

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
        mock_response = {
            # 1739974740 (14:19) is the settlement-witness candle proving
            # the last grid point's 1-min candle has closed.
            "t": [1739974320, 1739974620, 1739974680, 1739974740],
            "c": [100000.23, 105000.55, 108000.867, 108500.0],
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

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
        mock_response = {
            # 1739974740 is the settlement-witness candle.
            "t": [1739974320, 1739974680, 1739974740],
            "c": [100000.23, 108000.867, 108500.0],
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

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

    def test_fetch_data_gap_3(self):
        # 1739974320 - 2025-02-19T14:12:00+00:00
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
        mock_response = {
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

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

            validator_request_eth = ValidatorRequest(
                asset="ETH",
                start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
                time_length=540,
                time_increment=120,
            )

            result = self.dataProvider.fetch_data(validator_request_eth)

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
        mock_response = {
            "t": [1739974680, 1739974740, 1739974800, 1739974860, 1739974920],
            "c": [108000.867, 99000.23, 97123.55, 105123.345, 107995.889],
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

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
        mock_response = {
            # 1739974980 (14:23) is the settlement-witness candle for the
            # local request below whose last grid point is 14:22.
            "t": [
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
            "c": [
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
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

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
        mock_response = {
            "t": [
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
            "c": [
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
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

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
        mock_response = {
            "t": [
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
            "c": [
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
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response

            validator_request = ValidatorRequest(
                asset="BTC",
                start_time=datetime.fromisoformat("2025-02-19T14:12:00+00:00"),
                time_length=600,
                time_increment=300,
            )

            result = self.dataProvider.fetch_data(validator_request)

            assert result == [100000.23, 105000.55, 105123.345]

    def test_fetch_data(self):
        # Live call — uses a recent window so the Pyth Pro Router (which
        # only retains a rolling history) actually has data. The shared
        # module-level `validator_request` is fine for the mocked tests
        # above but its hardcoded 2025-02 date is outside the live window.
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

    def test_raises_when_no_candle_past_last_grid(self):
        data = {
            "t": [1739974320, 1739974440, 1739974560, 1739974680],
            "c": [1.0, 2.0, 3.0, 4.0],
        }
        with self.assertRaises(ValueError):
            PriceDataProvider._assert_settled(
                data, "BTC", "req-1", last_grid_timestamp=1739974680
            )

    def test_raises_when_no_candles_at_all(self):
        data = {"t": [], "c": []}
        with self.assertRaises(ValueError):
            PriceDataProvider._assert_settled(
                data, "BTC", "req-1", last_grid_timestamp=1739974680
            )

    def test_accepts_when_witness_candle_present(self):
        data = {
            "t": [1739974320, 1739974680, 1739974740],
            "c": [1.0, 4.0, 5.0],
        }
        # Should not raise.
        PriceDataProvider._assert_settled(
            data, "BTC", "req-1", last_grid_timestamp=1739974680
        )


class TestPriceDataProviderProBackend(unittest.TestCase):
    def test_pro_backend_uses_pro_url(self):
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

        provider = PriceDataProvider()
        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            result = provider.fetch_data(validator_request)

            called_params = mock_get.call_args.kwargs["params"]
            # The fetch window must extend one minute past the last grid
            # point so the settlement witness can land in the response.
            assert called_params["to"] == 1739974680 + 60
            assert result == [100000.23, 99000.55, 103000.55, 108000.867]


class TestPriceDataProviderLiveProBackend(unittest.TestCase):
    """Hits the live Pyth Pro Router history endpoint for every Pyth-routed
    asset — no mocks. The endpoint is public, so no PYTH_API_KEY is
    required. Catches channel/symbol regressions per asset (e.g. the 404s
    we saw on stocks/metals when the URL channel was real_time)."""

    def test_live_history_from_pro_router_per_asset(self):
        end = datetime.now(timezone.utc).replace(
            second=0, microsecond=0
        ) - timedelta(minutes=5)
        start = end - timedelta(minutes=10)

        provider = PriceDataProvider()

        for asset in PriceDataProvider.PYTH_SYMBOL_MAP.keys():
            with self.subTest(asset=asset):
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
                    self.assertLess(
                        p, 10_000_000, f"{asset}: suspicious magnitude"
                    )

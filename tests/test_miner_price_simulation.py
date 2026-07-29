import unittest
from unittest.mock import patch, MagicMock

from synth.miner import price_simulation
from synth.miner.price_simulation import (
    BINANCE_ASSET_MAP,
    HYPERLIQUID_ASSET_MAP,
    get_asset_price,
)

from synth.validator.price_data_provider import PriceDataProvider


class TestGetAssetPriceUnsupported(unittest.TestCase):
    def test_unmapped_asset_raises(self):
        assert "SPYX" not in BINANCE_ASSET_MAP
        assert "SPYX" not in HYPERLIQUID_ASSET_MAP
        # Unwrapped to skip the tenacity retries around the raise.
        with self.assertRaises(ValueError):
            get_asset_price.__wrapped__("SPYX")


class TestGetAssetPriceBinance(unittest.TestCase):
    def test_crypto_major_routes_to_binance_ticker(self):
        assert "BTC" not in HYPERLIQUID_ASSET_MAP
        assert BINANCE_ASSET_MAP["BTC"] == "BTCUSDT"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "symbol": "BTCUSDT",
            "price": "63128.00",
        }
        with patch("requests.get", return_value=mock_resp) as mock_get:
            price = get_asset_price("BTC")

        assert price == 63128.0
        called_url = mock_get.call_args[0][0]
        assert called_url == price_simulation.binance_ticker_url
        assert mock_get.call_args.kwargs["params"] == {"symbol": "BTCUSDT"}


class TestGetAssetPriceHyperliquid(unittest.TestCase):
    @staticmethod
    def _candles_resp(close: str) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"t": 1, "c": "1.0"},
            {"t": 2, "c": close},
        ]
        return mock_resp

    def _assert_routes_to_hyperliquid(self, asset: str, coin: str, close: str):
        resp = self._candles_resp(close)
        with patch("requests.post", return_value=resp) as mock_post:
            price = get_asset_price(asset)

        assert price == float(close)

        called_url = mock_post.call_args[0][0]
        assert called_url == price_simulation.hyperliquid_base_url
        body = mock_post.call_args.kwargs["json"]
        assert body["type"] == "candleSnapshot"
        assert body["req"]["coin"] == coin
        assert body["req"]["interval"] == "1m"

    def test_hype_routes_to_hyperliquid_spot(self):
        self._assert_routes_to_hyperliquid("HYPE", "@107", "60.10")

    def test_equity_routes_to_xyz_dex(self):
        self._assert_routes_to_hyperliquid("NVDAX", "xyz:NVDA", "206.70")

    def test_sp500_routes_to_xyz_dex(self):
        self._assert_routes_to_hyperliquid("SP500", "xyz:SP500", "7523.0")

    def test_wtioil_routes_to_hyperliquid(self):
        self._assert_routes_to_hyperliquid("WTIOIL", "xyz:CL", "65.00")

    def test_spcx_routes_to_hyperliquid(self):
        self._assert_routes_to_hyperliquid("SPCX", "xyz:SPCX", "187.08")

    def test_maps_match_validator(self):
        # Miners must fetch spot from the exact feed the validator scores
        # against.
        assert HYPERLIQUID_ASSET_MAP == PriceDataProvider.HYPERLIQUID_ASSET_MAP
        assert BINANCE_ASSET_MAP == PriceDataProvider.BINANCE_ASSET_MAP


if __name__ == "__main__":
    unittest.main()

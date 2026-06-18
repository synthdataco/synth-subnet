import os
import unittest
from unittest.mock import patch, MagicMock

from synth.miner import price_simulation
from synth.miner.price_simulation import (
    HYPERLIQUID_ASSET_MAP,
    LAZER_FEED_ID_MAP,
    TOKEN_MAP,
    get_asset_price,
)


class TestGetAssetPriceHermes(unittest.TestCase):
    def test_without_api_key_reads_hermes(self):
        # No PYTH_API_KEY -> the keyless Hermes endpoint, even for assets
        # that also have a Lazer feed.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "parsed": [{"price": {"price": "7930115688547", "expo": "-8"}}]
        }
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("PYTH_API_KEY", None)
            with patch("requests.get", return_value=mock_resp) as mock_get:
                price = get_asset_price("BTC")
                called_url = mock_get.call_args[0][0]

        assert called_url == price_simulation.pyth_base_url
        assert price == 79301.15688547


class TestGetAssetPriceLazer(unittest.TestCase):
    def test_with_api_key_posts_lazer_with_bearer(self):
        # PYTH_API_KEY present -> Lazer (paid) for assets with a Lazer feed.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "parsed": {
                "priceFeeds": [
                    {
                        "priceFeedId": LAZER_FEED_ID_MAP["BTC"],
                        "price": "7930115688547",
                        "exponent": -8,
                    }
                ]
            }
        }

        with patch.dict("os.environ", {"PYTH_API_KEY": "test-token"}):
            with patch("requests.post", return_value=mock_resp) as mock_post:
                price = get_asset_price("BTC")

        assert price == 79301.15688547

        called_url = mock_post.call_args[0][0]
        assert called_url == price_simulation.lazer_base_url

        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer test-token"}
        body = kwargs["json"]
        assert body["channel"] == "fixed_rate@200ms"
        assert body["priceFeedIds"] == [LAZER_FEED_ID_MAP["BTC"]]
        assert body["parsed"] is True


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

    def test_wtioil_routes_to_hyperliquid(self):
        # WTIOIL has no usable Lazer feed; the miner pulls it from
        # Hyperliquid (same coin the validator scores against), key or not.
        assert "WTIOIL" not in LAZER_FEED_ID_MAP
        assert HYPERLIQUID_ASSET_MAP["WTIOIL"] == "xyz:CL"

        resp = self._candles_resp("65.00")
        with patch.dict("os.environ", {"PYTH_API_KEY": "test-token"}):
            with patch("requests.get") as mock_get:
                with patch("requests.post", return_value=resp) as mock_post:
                    price = get_asset_price("WTIOIL")

        assert price == 65.0

        # Hyperliquid hit, neither Hermes nor Lazer touched.
        mock_get.assert_not_called()
        called_url = mock_post.call_args[0][0]
        assert called_url == price_simulation.hyperliquid_base_url
        body = mock_post.call_args.kwargs["json"]
        assert body["type"] == "candleSnapshot"
        assert body["req"]["coin"] == "xyz:CL"
        assert body["req"]["interval"] == "1m"

    def test_spcx_routes_to_hyperliquid_even_with_key(self):
        # SPCX is not on Hermes and has no Lazer feed — it exists only on
        # Hyperliquid, so it must resolve there regardless of PYTH_API_KEY.
        assert "SPCX" not in TOKEN_MAP
        assert "SPCX" not in LAZER_FEED_ID_MAP
        assert HYPERLIQUID_ASSET_MAP["SPCX"] == "xyz:SPCX"

        resp = self._candles_resp("187.08")
        with patch.dict("os.environ", {"PYTH_API_KEY": "test-token"}):
            with patch("requests.get") as mock_get:
                with patch("requests.post", return_value=resp) as mock_post:
                    price = get_asset_price("SPCX")

        assert price == 187.08

        mock_get.assert_not_called()
        called_url = mock_post.call_args[0][0]
        assert called_url == price_simulation.hyperliquid_base_url
        body = mock_post.call_args.kwargs["json"]
        assert body["type"] == "candleSnapshot"
        assert body["req"]["coin"] == "xyz:SPCX"
        assert body["req"]["interval"] == "1m"


if __name__ == "__main__":
    unittest.main()

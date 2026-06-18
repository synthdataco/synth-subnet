import os
import time

import requests


import numpy as np
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

# Hermes Pyth API documentation: https://hermes.pyth.network/docs/
# Pyth Lazer https://docs.pyth.network/price-feeds/pro/api/rest#post-v1latest_price
# If env: PYTH_API_KEY=<api key> is set, the miner will use Pyth Lazer instead of Hermes for assets with a Lazer feed.

TOKEN_MAP = {
    "BTC": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "XAU": "44465e17d2e9d390e70c999d5a11fda4f092847fcd2e3e5aa089d96c98a30e67",
    "SOL": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "SPYX": "2817b78438c769357182c04346fddaad1178c82f4048828fe0997c3c64624e14",
    "NVDAX": "4244d07890e4610f46bbde67de8f43a4bf8b569eebe904f136b469f148503b7f",
    "TSLAX": "47a156470288850a440df3a6ce85a55917b813a19bb5b31128a33a986566a362",
    "AAPLX": "978e6cc68a119ce066aa830017318563a9ed04ec3a0a6439010fc11296a58675",
    "GOOGLX": "b911b0329028cd0283e4259c33809d62942bd2716a58084e5f31d64c00b5424e",
    "XRP": "ec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "HYPE": "4279e31cc369bbcc2faf022b382b080e32a8e689ff20fbc530d2a603eb6cd98b",
    "WTIOIL": "67784f72e95ac01337edb7d7bd5bbd1c03669101b7068a620df228ed4e52ef14",
}

# Lazer u32 feed IDs sourced from https://pyth.dourolabs.app/v1/symbols by
# matching each asset's hermes_id.
LAZER_FEED_ID_MAP: dict[str, int] = {
    "BTC": 1,
    "ETH": 2,
    "SOL": 6,
    "XRP": 14,
    "HYPE": 110,
    "XAU": 346,
    "AAPLX": 1792,
    "GOOGLX": 1808,
    "NVDAX": 1833,
    "SPYX": 1843,
    "TSLAX": 1847,
}

# Hyperliquid `coin` codes for assets that have no usable Pyth feed.
HYPERLIQUID_ASSET_MAP = {
    "WTIOIL": "xyz:CL",
    "SPCX": "xyz:SPCX",
}

# `fixed_rate@200ms` meets every feed's min_channel (crypto majors accept
# `real_time`, but stocks/metals/commodities require `fixed_rate@200ms` or
# slower). Using one channel for every feed keeps the request body simple.
LAZER_CHANNEL = "fixed_rate@200ms"

pyth_base_url = "https://hermes.pyth.network/v2/updates/price/latest"
lazer_base_url = "https://pyth-lazer.dourolabs.app/v1/latest_price"
hyperliquid_base_url = "https://api.hyperliquid.xyz/info"


def _fetch_price_hermes(asset: str) -> float | None:
    pyth_params = {"ids[]": [TOKEN_MAP[asset]]}
    response = requests.get(pyth_base_url, params=pyth_params)
    if response.status_code != 200:
        print("Error in response of Pyth API")
        return None

    data = response.json()
    parsed_data = data.get("parsed", [])

    feed = parsed_data[0]
    price = int(feed["price"]["price"])
    expo = int(feed["price"]["expo"])

    live_price: float = price * (10**expo)
    return live_price


def _fetch_price_lazer(asset: str, api_key: str) -> float | None:
    payload = {
        "channel": LAZER_CHANNEL,
        "priceFeedIds": [LAZER_FEED_ID_MAP[asset]],
        "properties": ["price", "exponent"],
        "formats": [],
        "parsed": True,
        "jsonBinaryEncoding": "hex",
    }
    response = requests.post(
        lazer_base_url,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if response.status_code != 200:
        print("Error in response of Pyth Lazer API")
        return None

    data = response.json()
    feeds = (data.get("parsed") or {}).get("priceFeeds") or []
    if not feeds:
        return None

    feed = feeds[0]
    price_mantissa = feed.get("price")
    expo = feed.get("exponent")
    if price_mantissa is None or expo is None:
        return None

    live_price: float = float(price_mantissa) * (10 ** int(expo))
    return live_price


def _fetch_price_hyperliquid(asset: str) -> float | None:
    now_ms = int(time.time() * 1000)
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": HYPERLIQUID_ASSET_MAP[asset],
            "interval": "1m",
            "startTime": now_ms - 5 * 60 * 1000,
            "endTime": now_ms,
        },
    }
    response = requests.post(hyperliquid_base_url, json=payload, timeout=30)
    if response.status_code != 200:
        print("Error in response of Hyperliquid API")
        return None

    candles = response.json()
    if not candles:
        return None

    return float(candles[-1]["c"])


@retry(
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(multiplier=2),
    reraise=True,
)
def get_asset_price(asset="BTC") -> float | None:
    if asset in HYPERLIQUID_ASSET_MAP:
        return _fetch_price_hyperliquid(asset)
    api_key = os.environ.get("PYTH_API_KEY")
    if asset in LAZER_FEED_ID_MAP and api_key:
        return _fetch_price_lazer(asset, api_key)
    return _fetch_price_hermes(asset)


def simulate_single_price_path(
    current_price: float, time_increment: int, time_length: int, sigma: float
) -> np.ndarray:
    """
    Simulate a single crypto asset price path.
    """
    one_hour = 3600
    dt = time_increment / one_hour
    num_steps = int(time_length / time_increment)
    std_dev = sigma * np.sqrt(dt)
    price_change_pcts = np.random.normal(0, std_dev, size=num_steps)
    cumulative_returns = np.cumprod(1 + price_change_pcts)
    cumulative_returns = np.insert(cumulative_returns, 0, 1.0)
    price_path = current_price * cumulative_returns

    return price_path


def simulate_crypto_price_paths(
    current_price: float,
    time_increment: int,
    time_length: int,
    num_simulations: int,
    sigma: float,
) -> np.ndarray:
    """
    Simulate multiple crypto asset price paths.
    """

    price_paths = []
    for _ in range(num_simulations):
        price_path = simulate_single_price_path(
            current_price, time_increment, time_length, sigma
        )
        price_paths.append(price_path)

    return np.array(price_paths)

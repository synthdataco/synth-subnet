import os
import time

import requests


import numpy as np
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

BINANCE_ASSET_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

HYPERLIQUID_ASSET_MAP = {
    # HL spot HYPE/USDC (spot coins are addressed by `@<index>`).
    "HYPE": "@107",
    "XAU": "xyz:GOLD",
    "NVDAX": "xyz:NVDA",
    "TSLAX": "xyz:TSLA",
    "AAPLX": "xyz:AAPL",
    "GOOGLX": "xyz:GOOGL",
    "SP500": "xyz:SP500",
    "SPCX": "xyz:SPCX",
    "WTIOIL": "xyz:CL",
}

hyperliquid_base_url = "https://api.hyperliquid.xyz/info"
# BINANCE_API_HOST is a process-env escape hatch (read at import time):
# api.binance.com returns HTTP 451 from geo-restricted regions, e.g. the
# US-hosted CI runners, which use data-api.binance.vision instead.
binance_ticker_url = (
    os.environ.get("BINANCE_API_HOST", "https://api.binance.com")
    + "/api/v3/ticker/price"
)


def _fetch_price_binance(asset: str) -> float | None:
    response = requests.get(
        binance_ticker_url,
        params={"symbol": BINANCE_ASSET_MAP[asset]},
        timeout=30,
    )
    if response.status_code != 200:
        print("Error in response of Binance API")
        return None

    return float(response.json()["price"])


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
    if asset in BINANCE_ASSET_MAP:
        return _fetch_price_binance(asset)
    if asset in HYPERLIQUID_ASSET_MAP:
        return _fetch_price_hyperliquid(asset)
    raise ValueError(f"unsupported asset {asset}")


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

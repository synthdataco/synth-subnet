import logging
import os
import time
import requests


from tenacity import (
    before_log,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)
import numpy as np
import bittensor as bt

from synth.db.models import ValidatorRequest
from synth.utils.helpers import from_iso_to_unix_time
from synth.utils.logging import print_execution_time

# Pyth API benchmarks doc: https://benchmarks.pyth.network/docs
# get the list of stocks supported by pyth: https://benchmarks.pyth.network/v1/shims/tradingview/symbol_info?group=pyth_stock
# get the list of crypto supported by pyth: https://benchmarks.pyth.network/v1/shims/tradingview/symbol_info?group=pyth_crypto
# get the ticker: https://benchmarks.pyth.network/v1/shims/tradingview/symbols?symbol=Crypto.XAUT/USD
#
# Pyth Pro Router (new) returns the same TradingView shape from
# https://pyth.dourolabs.app/v1/real_time/history with the same `Crypto.*/USD`
# symbol strings — public, no auth required. Selected by PYTH_BACKEND=pro.


class PriceDataProvider:
    PYTH_BENCHMARKS_URL = (
        "https://benchmarks.pyth.network/v1/shims/tradingview/history"
    )
    # `fixed_rate@200ms` is the channel that meets every feed's min_channel:
    # stocks/metals/oil reject `real_time` with 404, but accept this. Crypto
    # majors accept it too. One channel, every feed.
    PYTH_PRO_URL = "https://pyth.dourolabs.app/v1/fixed_rate@200ms/history"
    HYPERLIQUID_BASE_URL = "https://api.hyperliquid.xyz/info"

    # Both Pyth and Hyperliquid serve 1-minute candles indexed by their open
    # timestamp; the candle at T is only final once time has passed T + 60s.
    # `CANDLE_INTERVAL_SECONDS` is the *structural* offset — exactly one
    # candle past the last scored grid point, which is where the settlement
    # witness lives. Asking Pyth for more (e.g. + 120s) doesn't make the
    # witness arrive sooner; it just widens the query window unnecessarily.
    #
    # The *operational* wait (one candle interval + Pyth's publish latency)
    # belongs to the scoring gate in
    # `miner_data_handler.SCORING_GATE_SECONDS` — that constant decides when
    # scoring is even attempted. This one only decides where to look for
    # the witness once we do attempt.
    CANDLE_INTERVAL_SECONDS = 60

    # Assets fetched from Pyth
    PYTH_SYMBOL_MAP = {
        "BTC": "Crypto.BTC/USD",
        "ETH": "Crypto.ETH/USD",
        "XAU": "Crypto.XAUT/USD",
        "SOL": "Crypto.SOL/USD",
        "SPYX": "Crypto.SPYX/USD",
        "NVDAX": "Crypto.NVDAX/USD",
        "TSLAX": "Crypto.TSLAX/USD",
        "AAPLX": "Crypto.AAPLX/USD",
        "GOOGLX": "Crypto.GOOGLX/USD",
        "XRP": "Crypto.XRP/USD",
        "HYPE": "Crypto.HYPE/USD",
        "SPCX": "Pyth.HL.SPCX/USDC",
    }

    # Assets fetched from Hyperliquid (overrides Pyth for these assets)
    HYPERLIQUID_SYMBOL_MAP = {
        "WTIOIL": "xyz:CL",
    }

    def __init__(self):
        # Resolve once at instance construction so we honour the .env loaded
        # by neurons/validator.py before instantiation. PYTH_BACKEND=pro
        # routes history through the new Pyth Pro Router; default `hermes`
        # keeps the legacy Benchmarks endpoint for instant rollback.
        backend = os.environ.get("PYTH_BACKEND", "hermes").lower()
        self.pyth_history_url = (
            self.PYTH_PRO_URL if backend == "pro" else self.PYTH_BENCHMARKS_URL
        )

    @staticmethod
    def assert_assets_supported(asset_list: list[str]):
        supported = (
            PriceDataProvider.PYTH_SYMBOL_MAP.keys()
            | PriceDataProvider.HYPERLIQUID_SYMBOL_MAP.keys()
        )
        for asset in asset_list:
            assert asset in supported

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=2),
        reraise=True,
        before=before_log(bt.logging._logger, logging.DEBUG),
    )
    @print_execution_time
    def fetch_data(self, validator_request: ValidatorRequest) -> list:
        """
        Fetch price data for the given request.
        Returns a list of close prices (float or NaN) aligned to the
        timestamp grid defined by start_time, time_length, and time_increment.
        """
        asset = str(validator_request.asset)

        prices = []

        if asset in self.HYPERLIQUID_SYMBOL_MAP:
            prices = self.fetch_data_hyperliquid(validator_request)
        else:
            start_time_int = from_iso_to_unix_time(
                validator_request.start_time.isoformat()
            )
            last_grid_timestamp = (
                start_time_int + validator_request.time_length
            )
            params = {
                "symbol": self.PYTH_SYMBOL_MAP[asset],
                "resolution": 1,
                "from": start_time_int,
                # Fetch one extra minute past the last grid point so we
                # can verify that candle has closed before scoring with it.
                "to": last_grid_timestamp + self.CANDLE_INTERVAL_SECONDS,
            }

            response = requests.get(self.pyth_history_url, params=params)
            response.raise_for_status()
            data = response.json()

            self._assert_settled(
                data,
                asset,
                validator_request.id,
                last_grid_timestamp,
            )

            prices = self._transform_data(
                data,
                start_time_int,
                int(validator_request.time_increment),
                int(validator_request.time_length),
            )

        if not prices or np.isnan(prices[-1]):
            bt.logging.warning(
                f"missing price data for the last timestamp for asset {asset} in request {validator_request.id}"
            )
            raise ValueError(
                f"missing price data for the last timestamp for asset {asset} in request {validator_request.id}"
            )

        return prices

    def fetch_data_hyperliquid(
        self, validator_request: ValidatorRequest
    ) -> list:
        start_time_int = from_iso_to_unix_time(
            validator_request.start_time.isoformat()
        )
        return self.download_hyperliquid_price_data(
            beginning=start_time_int,
            end=start_time_int + int(validator_request.time_length),
            symbol=str(validator_request.asset),
            time_increment=int(validator_request.time_increment),
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=2),
        reraise=True,
        before=before_log(bt.logging._logger, logging.DEBUG),
    )
    def download_hyperliquid_price_data(
        self,
        beginning: int,  # Unix timestamp in seconds
        end: int,  # Unix timestamp in seconds
        symbol: str = "WTIOIL",
        time_increment: int = 60,
        loop_wait_time_seconds: float = 0.1,
    ) -> list:
        MAX_CANDLES = 5000
        INTERVAL_MS = 60 * 1000  # 1 minute in ms
        chunk_ms = MAX_CANDLES * INTERVAL_MS

        beginning_ms = beginning * 1000
        end_ms = end * 1000
        # Same settlement guard as the Pyth path: extend the request by one
        # extra minute so we can verify the candle at `end_ms` has closed.
        settlement_witness_ms = end_ms + self.CANDLE_INTERVAL_SECONDS * 1000
        candles = []
        saw_settled_witness = False

        with requests.Session() as session:
            current_start = beginning_ms
            while current_start < settlement_witness_ms:
                current_end = min(
                    current_start + chunk_ms, settlement_witness_ms
                )

                payload = {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": self.HYPERLIQUID_SYMBOL_MAP[symbol],
                        "interval": "1m",
                        "startTime": current_start,
                        "endTime": current_end,
                    },
                }

                response = session.post(
                    self.HYPERLIQUID_BASE_URL, json=payload, timeout=100
                )
                response.raise_for_status()
                data = response.json()

                bt.logging.debug(
                    f"Fetched {len(data)} candles for {symbol} [{current_start}, {current_end}]"
                )

                for candle in data:
                    t = int(candle["t"])
                    if beginning_ms <= t <= end_ms:
                        candles.append(candle)
                    if t > end_ms:
                        saw_settled_witness = True

                current_start += chunk_ms
                time.sleep(loop_wait_time_seconds)

        if not saw_settled_witness:
            bt.logging.warning(
                f"realized path not yet settled for asset {symbol}: no "
                f"Hyperliquid candle with t > {end_ms} ms"
            )
            raise ValueError(
                f"realized path not yet settled for asset {symbol}"
            )

        if not candles:
            bt.logging.warning(
                f"No data returned from Hyperliquid for {symbol}"
            )
            return []

        normalized = {
            "t": [candle["t"] // 1000 for candle in candles],
            "c": [float(candle["c"]) for candle in candles],
        }
        return self._transform_data(
            normalized, beginning, time_increment, end - beginning
        )

    @staticmethod
    def _assert_settled(
        data: dict,
        asset: str,
        request_id,
        last_grid_timestamp: int,
    ) -> None:
        """Raise if the response does not prove the final scored candle has
        closed. The witness is any candle with timestamp strictly greater
        than `last_grid_timestamp` — it can only exist after that grid
        point's 1-minute candle has finished."""
        timestamps = (data or {}).get("t") or []
        max_t = max(timestamps) if timestamps else None
        if max_t is not None and max_t > last_grid_timestamp:
            return
        bt.logging.warning(
            f"realized path not yet settled for asset {asset} in request "
            f"{request_id}: max candle t={max_t}, need > "
            f"{last_grid_timestamp}"
        )
        raise ValueError(
            f"realized path not yet settled for asset {asset} in request "
            f"{request_id}"
        )

    @staticmethod
    def _transform_data(
        data, start_time_int: int, time_increment: int, time_length: int
    ) -> list:
        if data is None or len(data) == 0 or len(data["t"]) == 0:
            return []

        time_end_int = start_time_int + time_length
        timestamps = list(
            range(
                start_time_int, time_end_int + time_increment, time_increment
            )
        )

        if len(timestamps) != int(time_length / time_increment) + 1:
            # Note: this part of code should never be activated; just included for precaution
            if len(timestamps) == int(time_length / time_increment) + 2:
                bt.logging.warning(
                    f"Unexpected number of timestamps generated. Expected {int(time_length / time_increment) + 1} but got {len(timestamps)}. Adjusting the timestamps list by removing the extra timestamp."
                )
                if data["t"][-1] < timestamps[1]:
                    timestamps = timestamps[:-1]
                elif data["t"][0] > timestamps[0]:
                    timestamps = timestamps[1:]
            else:
                return []

        close_prices_dict = {t: c for t, c in zip(data["t"], data["c"])}
        result = [np.nan] * len(timestamps)
        for idx, t in enumerate(timestamps):
            if t in close_prices_dict:
                result[idx] = close_prices_dict[t]

        return result

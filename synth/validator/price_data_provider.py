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


class PriceDataProvider:
    HYPERLIQUID_BASE_URL = "https://api.hyperliquid.xyz/info"
    # BINANCE_API_HOST is a process-env escape hatch (read at import time):
    # api.binance.com returns HTTP 451 from geo-restricted regions, e.g.
    # the US-hosted CI runners, which use data-api.binance.vision instead.
    BINANCE_SPOT_URL = (
        os.environ.get("BINANCE_API_HOST", "https://api.binance.com")
        + "/api/v3/klines"
    )

    # Hyperliquid serves 1-minute candles indexed by their open
    # timestamp; the candle at T is only final once time has passed T + 60s.
    # `CANDLE_INTERVAL_SECONDS` is the *structural* offset — exactly one
    # candle past the last scored grid point, which is where the settlement
    # witness lives. Asking the source for more (e.g. + 120s) doesn't make
    # the witness arrive sooner; it just widens the query window
    # unnecessarily.
    #
    # The *operational* wait (one candle interval + publish latency)
    # belongs to the scoring gate in
    # `miner_data_handler.SCORING_GATE_SECONDS` — that constant decides when
    # scoring is even attempted. This one only decides where to look for
    # the witness once we do attempt.
    CANDLE_INTERVAL_SECONDS = 60

    BINANCE_ASSET_MAP = {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "SOL": "SOLUSDT",
        "XRP": "XRPUSDT",
    }

    HYPERLIQUID_ASSET_MAP = {
        # HL spot HYPE/USDC (spot coins are addressed by `@<index>` in
        # candleSnapshot, same endpoint as perps).
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

    @staticmethod
    def assert_assets_supported(asset_list: list[str]):
        supported = (
            PriceDataProvider.BINANCE_ASSET_MAP.keys()
            | PriceDataProvider.HYPERLIQUID_ASSET_MAP.keys()
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

        if asset in self.BINANCE_ASSET_MAP:
            prices = self.fetch_data_binance(validator_request)
        elif asset in self.HYPERLIQUID_ASSET_MAP:
            prices = self.fetch_data_hyperliquid(validator_request)
        else:
            raise ValueError(
                f"unsupported asset {asset} in request {validator_request.id}"
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

    def fetch_data_binance(self, validator_request: ValidatorRequest) -> list:
        start_time_int = from_iso_to_unix_time(
            validator_request.start_time.isoformat()
        )
        return self.download_binance_price_data(
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
    def download_binance_price_data(
        self,
        beginning: int,  # Unix timestamp in seconds
        end: int,  # Unix timestamp in seconds
        symbol: str,
        time_increment: int = 60,
        loop_wait_time_seconds: float = 0.1,
    ) -> list:
        MAX_KLINES = 1000
        INTERVAL_MS = 60 * 1000  # 1 minute in ms
        chunk_ms = MAX_KLINES * INTERVAL_MS

        beginning_ms = beginning * 1000
        end_ms = end * 1000
        # Settlement guard: extend the request by one extra minute so we
        # can verify the kline at `end_ms` has closed. Binance prints
        # klines eagerly every minute (even with zero trades), so the
        # witness semantics match the Hyperliquid path.
        settlement_witness_ms = end_ms + self.CANDLE_INTERVAL_SECONDS * 1000
        klines = []
        saw_settled_witness = False

        with requests.Session() as session:
            current_start = beginning_ms
            while current_start < settlement_witness_ms:
                current_end = min(
                    current_start + chunk_ms - INTERVAL_MS,
                    settlement_witness_ms,
                )

                params = {
                    "symbol": self.BINANCE_ASSET_MAP[symbol],
                    "interval": "1m",
                    "startTime": current_start,
                    "endTime": current_end,
                    "limit": MAX_KLINES,
                }

                response = session.get(
                    self.BINANCE_SPOT_URL, params=params, timeout=30
                )
                response.raise_for_status()
                data = response.json()

                bt.logging.debug(
                    f"Fetched {len(data)} klines for {symbol} [{current_start}, {current_end}]"
                )

                for kline in data:
                    t = int(kline[0])  # open time in ms
                    if beginning_ms <= t <= end_ms:
                        klines.append(kline)
                    if t > end_ms:
                        saw_settled_witness = True

                current_start += chunk_ms
                time.sleep(loop_wait_time_seconds)

        if not saw_settled_witness:
            bt.logging.warning(
                f"realized path not yet settled for asset {symbol}: no "
                f"Binance kline with open time > {end_ms} ms"
            )
            raise ValueError(
                f"realized path not yet settled for asset {symbol}"
            )

        if not klines:
            bt.logging.warning(f"No data returned from Binance for {symbol}")
            return []

        normalized = {
            "t": [int(kline[0]) // 1000 for kline in klines],
            "c": [float(kline[4]) for kline in klines],
        }
        return self._transform_data(
            normalized, beginning, time_increment, end - beginning
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
        symbol: str,
        time_increment: int = 60,
        loop_wait_time_seconds: float = 0.1,
    ) -> list:
        MAX_CANDLES = 5000
        INTERVAL_MS = 60 * 1000  # 1 minute in ms
        chunk_ms = MAX_CANDLES * INTERVAL_MS

        beginning_ms = beginning * 1000
        end_ms = end * 1000
        # Settlement guard: extend the request by one extra minute so we
        # can verify the candle at `end_ms` has closed.
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
                        "coin": self.HYPERLIQUID_ASSET_MAP[symbol],
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

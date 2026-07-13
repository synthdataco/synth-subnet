from datetime import datetime, timedelta
import time


import bittensor as bt


from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.prompt_config import PromptConfig
from synth.utils.helpers import get_current_time, round_time_to_minutes


class SequentialScheduler:
    def __init__(
        self,
        prompt_config: PromptConfig,
        target: callable,
        miner_data_handler: MinerDataHandler,
    ):
        self.prompt_config = prompt_config
        self.target = target
        self.miner_data_handler = miner_data_handler
        self.first_run = True

    def start(self):
        cycle_start_time = get_current_time()
        while True:
            try:
                cycle_start_time = self.run_cycle(cycle_start_time)
            except Exception:
                bt.logging.exception("Error in cycle ")
                cycle_start_time = get_current_time()
            self.first_run = False

    def run_cycle(
        self,
        cycle_start_time: datetime,
    ):
        prompt_config = self.prompt_config

        asset_list = prompt_config.asset_list

        delay = self.select_delay(
            cycle_start_time,
            prompt_config,
            self.first_run,
        )
        latest_asset = self.miner_data_handler.get_latest_asset(
            prompt_config.time_length
        )
        asset = self.select_asset(latest_asset, asset_list)

        bt.logging.info(
            f"Scheduling next {prompt_config.label} frequency cycle for asset {asset} in {delay} seconds"
        )

        time.sleep(delay)
        cycle_start_time = get_current_time()
        self.target(asset)

        return cycle_start_time

    @staticmethod
    def select_delay(
        cycle_start_time: datetime,
        prompt_config: PromptConfig,
        first_run: bool = False,
    ) -> int:
        offset = prompt_config.cycle_offset_minutes
        if offset is not None:
            return SequentialScheduler.select_grid_delay(
                cycle_start_time, prompt_config, offset, first_run
            )

        next_cycle = cycle_start_time
        next_cycle = round_time_to_minutes(next_cycle)
        if not first_run:
            next_cycle += timedelta(
                minutes=prompt_config.cycle_interval_minutes
            )
            next_cycle = next_cycle - timedelta(minutes=1)
        next_cycle_diff = next_cycle - get_current_time()
        delay = int(next_cycle_diff.total_seconds())
        if delay <= 0:
            bt.logging.warning("Calculated delay is non-positive")
            current_time = get_current_time()
            diff = round_time_to_minutes(current_time) - current_time
            delay = int(diff.total_seconds())

        return delay

    @staticmethod
    def select_grid_delay(
        cycle_start_time: datetime,
        prompt_config: PromptConfig,
        offset: int,
        first_run: bool = False,
    ) -> int:
        """Delay until the next minute boundary T where
        minutes-since-epoch(T) % cycle_interval_minutes == offset.
        """
        interval = prompt_config.cycle_interval_minutes

        now = get_current_time()
        next_boundary = round_time_to_minutes(now)
        boundary_minutes = int(next_boundary.timestamp()) // 60
        next_cycle = next_boundary + timedelta(
            minutes=(offset - boundary_minutes) % interval
        )

        if not first_run and next_cycle - cycle_start_time > timedelta(
            minutes=interval
        ):
            bt.logging.warning(
                "Cycle overran its slot, skipping to the next one in its lane"
            )

        return int((next_cycle - now).total_seconds())

    @staticmethod
    def select_asset(latest_asset: str | None, asset_list: list[str]) -> str:
        asset = asset_list[0]

        if latest_asset is not None and latest_asset in asset_list:
            latest_index = asset_list.index(latest_asset)
            asset = asset_list[(latest_index + 1) % len(asset_list)]

        return asset

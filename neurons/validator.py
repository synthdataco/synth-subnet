# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Mode Labs
import time
from datetime import datetime
import multiprocessing as mp

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.


from dotenv import load_dotenv
import bittensor as bt

from synth.base.validator import BaseValidatorNeuron

from synth.simulation_input import SimulationInput
from synth.utils.helpers import (
    get_current_time,
    metagraph_refresh_due,
    round_time_to_minutes,
)
from synth.utils.logging import print_execution_time, setup_gcp_logging
from synth.utils.sequential_scheduler import SequentialScheduler
from synth.validator.forward import (
    calculate_moving_average_and_update_rewards,
    calculate_scores,
    get_available_miners_and_update_metagraph_history,
    query_available_miners_and_save_responses,
    send_weights_to_bittensor_and_update_weights_history,
)
from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator.storage_backend import STORAGE_BACKEND_BIGTABLE
from synth.validator import competition_config
from synth.validator.prompt_config import (
    PromptConfig,
    LOW_FREQUENCY,
    HIGH_FREQUENCY,
)

load_dotenv()

CYCLE_LOW_FREQUENCY = "low_frequency"
CYCLE_HIGH_FREQUENCY = "high_frequency"
CYCLE_SCORING = "scoring"
CYCLE_FULL = "full"

# Metagraph schedule (each cycle runs as its own process, with its own
# timer). The chain sync keeps miner_uids/axon addresses/miner identities
# fresh for querying; the metagraph_history snapshot feeds downstream
# analytics that read it hourly, so minute-level snapshots are pure table
# growth. Only the scoring process writes snapshots.
METAGRAPH_SYNC_INTERVAL_MINUTES = 15
METAGRAPH_SNAPSHOT_INTERVAL_MINUTES = 30


class Validator(BaseValidatorNeuron):
    """
    Your validator neuron class. You should use this class to define your validator's behavior. In particular, you should replace the forward function with your own logic.

    This class inherits from the BaseValidatorNeuron class, which in turn inherits from BaseNeuron. The BaseNeuron class takes care of routine tasks such as setting up wallet, subtensor, metagraph, logging directory, parsing config, etc. You can override any of the methods in BaseNeuron if you need to customize the behavior.

    This class provides reasonable default behavior for a validator such as keeping a moving average of the scores of the miners and using them to set weights at the end of each epoch. Additionally, the scores are reset for new hotkeys at the end of each epoch.
    """

    def __init__(self, config=None, cycle_name: str = CYCLE_LOW_FREQUENCY):
        super(Validator, self).__init__(config=config)
        self.cycle_name = cycle_name

        setup_gcp_logging(self.config.gcp.log_id_prefix)

        bt.logging.info("load_state()", "__init__")
        self.load_state()

        bigtable_storage = None
        if self.config.storage.backend == STORAGE_BACKEND_BIGTABLE:
            # Lazy import keeps google-cloud-bigtable optional for
            # validators that stay on the Postgres backend.
            from synth.validator.bigtable_prediction_storage import (
                BigtablePredictionStorage,
            )

            bigtable_storage = BigtablePredictionStorage()
        self.miner_data_handler = MinerDataHandler(
            bigtable_storage=bigtable_storage
        )
        self.price_data_provider = PriceDataProvider()

        self.miner_uids: list[int] = []
        self.last_metagraph_refresh_at: datetime | None = None
        LOW_FREQUENCY.data_retention_days = self.config.retention.low.days
        HIGH_FREQUENCY.data_retention_days = self.config.retention.high.days
        LOW_FREQUENCY.cycle_interval_minutes = (
            self.config.cycle_interval_minutes.low
        )
        HIGH_FREQUENCY.cycle_interval_minutes = (
            self.config.cycle_interval_minutes.high
        )

        PriceDataProvider.assert_assets_supported(HIGH_FREQUENCY.asset_list)
        PriceDataProvider.assert_assets_supported(LOW_FREQUENCY.asset_list)

        if self.config.validator.cycle_name != "":
            bt.logging.info(
                f"Overriding cycle_name from config: {self.config.validator.cycle_name}"
            )
            self.cycle_name = self.config.validator.cycle_name

        self._apply_assets_filter()

    def _apply_assets_filter(self):
        """Narrow the active cycle's asset_list to --validator.assets, if set.

        No-op for the scoring cycle or when the flag is unset. Raises
        ValueError on assets not in the active cycle's asset_list.
        """
        assets_filter = self.config.validator.assets
        if assets_filter == "":
            return

        if self.cycle_name == CYCLE_LOW_FREQUENCY:
            target_config: PromptConfig = LOW_FREQUENCY
        elif self.cycle_name == CYCLE_HIGH_FREQUENCY:
            target_config = HIGH_FREQUENCY
        else:
            return

        requested = [a.strip() for a in assets_filter.split(",") if a.strip()]
        unknown = set(requested) - set(target_config.asset_list)
        if unknown:
            raise ValueError(
                f"--validator.assets contains unknown assets {sorted(unknown)} "
                f"for {target_config.label}-frequency cycle. "
                f"Valid: {target_config.asset_list}"
            )
        target_config.asset_list = [
            a for a in target_config.asset_list if a in requested
        ]
        bt.logging.info(
            f"Restricting {target_config.label}-frequency cycle to assets: "
            f"{target_config.asset_list}"
        )

    def forward_validator(self):
        """Boot metagraph refresh, then run this process's cycle forever."""
        self.refresh_metagraph(interval_minutes=0)  # boot: always due
        if self.cycle_name == CYCLE_LOW_FREQUENCY:
            SequentialScheduler(
                prompt_config=LOW_FREQUENCY,
                target=self.cycle_low_frequency,
                miner_data_handler=self.miner_data_handler,
            ).start()
        elif self.cycle_name == CYCLE_HIGH_FREQUENCY:
            SequentialScheduler(
                prompt_config=HIGH_FREQUENCY,
                target=self.cycle_high_frequency,
                miner_data_handler=self.miner_data_handler,
            ).start()
        elif self.cycle_name == CYCLE_SCORING:
            self.cycle_scoring()
        else:
            raise ValueError(f"Unknown cycle name: {self.cycle_name}")

    @print_execution_time
    def cycle_low_frequency(self, asset: str):
        bt.logging.info(
            "starting the low frequency cycle", "cycle_low_frequency"
        )
        self.forward_prompt(asset, LOW_FREQUENCY)
        # Refresh after the prompt: the scheduler fires just before a minute
        # boundary, and a chain sync beforehand would push start_time late.
        self.refresh_metagraph(METAGRAPH_SYNC_INTERVAL_MINUTES)

    @print_execution_time
    def cycle_scoring(self):
        bt.logging.info("starting the scoring cycle", "cycle_scoring")
        while True:
            self.forward_score()
            self.refresh_metagraph(METAGRAPH_SNAPSHOT_INTERVAL_MINUTES)
            time.sleep(5)

    @print_execution_time
    def cycle_high_frequency(self, asset: str):
        bt.logging.info(
            "starting the high frequency cycle", "cycle_high_frequency"
        )
        self.forward_prompt(asset, HIGH_FREQUENCY)
        self.refresh_metagraph(METAGRAPH_SYNC_INTERVAL_MINUTES)

    def refresh_metagraph(self, interval_minutes: int):
        """Sync the chain metagraph — refreshing miner_uids and upserting
        miner identities in the same call, so a newly queryable uid always
        has its miners row — at most every interval_minutes. Only the
        scoring process also appends a metagraph_history snapshot. Each
        cycle runs as its own process, so one timestamp suffices.

        An empty result (degenerate chain response) is applied — the shared
        metagraph object was just emptied by the sync, so stale uids must
        not outlive it — but not stamped, so the next cycle retries.

        A sync that raises (e.g. the chain endpoint rate-limiting with
        HTTP 429) is swallowed: the previous miner set stays in use and,
        because the timer is not stamped, the next cycle retries. This
        keeps a transient chain outage from killing the process — the
        boot call and the scoring loop have no other safety net.
        """
        now = get_current_time()
        if not metagraph_refresh_due(
            self.last_metagraph_refresh_at, now, interval_minutes
        ):
            return
        try:
            self.miner_uids = (
                get_available_miners_and_update_metagraph_history(
                    base_neuron=self,
                    miner_data_handler=self.miner_data_handler,
                    save_snapshot=self.cycle_name == CYCLE_SCORING,
                )
            )
        except Exception:
            bt.logging.exception(
                "Metagraph refresh failed; keeping the previous miner set"
            )
            return
        if self.miner_uids:
            self.last_metagraph_refresh_at = now

    @print_execution_time
    def forward_prompt(self, asset: str, prompt_config: PromptConfig):
        bt.logging.info(
            f"forward prompt for {asset} in {prompt_config.label} frequency",
            "forward_prompt",
        )
        if len(self.miner_uids) == 0:
            bt.logging.error(
                "No miners available",
                "forward_prompt",
            )
            return

        request_time = get_current_time()
        start_time: datetime = round_time_to_minutes(
            request_time, prompt_config.timeout_extra_seconds
        )

        simulation_input = SimulationInput(
            asset=asset,
            start_time=start_time.isoformat(),
            time_increment=prompt_config.time_increment,
            time_length=prompt_config.time_length,
            num_simulations=prompt_config.num_simulations,
        )

        query_available_miners_and_save_responses(
            base_neuron=self,
            miner_data_handler=self.miner_data_handler,
            miner_uids=self.miner_uids,
            simulation_input=simulation_input,
            request_time=request_time,
        )

    @print_execution_time
    def forward_score(self):
        # ================= Step 3 ================= #
        # Calculate rewards based on historical predictions data
        # from the miner_predictions table:
        # we're going to get the predictions that are already in the past,
        # in this way we know the real prices, can compare them
        # with predictions and calculate the rewards,
        # we store the rewards in the miner_scores table
        # ========================================== #
        current_time = get_current_time()
        scored_time: datetime = round_time_to_minutes(current_time)

        success_count = 0
        for comp in competition_config.ALL_COMPETITIONS:
            bt.logging.info(f"forward score {comp.label}", "forward_score")

            if calculate_scores(
                self.miner_data_handler,
                self.price_data_provider,
                scored_time,
                comp,
                self.config.neuron.nprocs,
            ):
                success_count += 1

        self.cleanup_history()

        if success_count == 0:
            return

        # ================= Step 4 ================= #
        # Calculate moving average based on the past results
        # in the miner_scores table and save them
        # in the miner_rewards table in the end
        # ========================================== #

        moving_averages_data = calculate_moving_average_and_update_rewards(
            miner_data_handler=self.miner_data_handler,
            scored_time=scored_time,
        )

        if len(moving_averages_data) == 0:
            return

        # ================= Step 5 ================= #
        # Send rewards calculated in the previous step
        # into bittensor consensus calculation
        # ========================================== #

        moving_averages_data.append(
            {
                "miner_id": 0,
                "miner_uid": (
                    23 if self.config.subtensor.network == "test" else 248
                ),
                "smoothed_score": 0,
                "reward_weight": sum(
                    [r["reward_weight"] for r in moving_averages_data]
                ),
                "updated_at": scored_time.isoformat(),
            }
        )

        bt.logging.info(
            f"Moving averages data for owner: {moving_averages_data[-1]}",
            "forward_score",
        )

        send_weights_to_bittensor_and_update_weights_history(
            base_neuron=self,
            moving_averages_data=moving_averages_data,
            miner_data_handler=self.miner_data_handler,
            scored_time=scored_time,
        )

    def cleanup_history(self):
        """Cleans up old history from the miner data handler."""
        self.miner_data_handler.density_tapering_predictions(HIGH_FREQUENCY)
        self.miner_data_handler.density_tapering_predictions(LOW_FREQUENCY)
        if self.config.validator.mode == "light":
            self.miner_data_handler.cleanup_old_history(HIGH_FREQUENCY)
            self.miner_data_handler.cleanup_old_history(LOW_FREQUENCY)

    async def forward_miner(self, _: bt.Synapse) -> bt.Synapse:
        pass


mp.set_start_method("spawn", force=True)


def spawn_validator(mode: str = ""):
    Validator(cycle_name=mode).run()


# The main function parses the configuration and runs the validator.
if __name__ == "__main__":
    spawn_validator()

    # processes = [
    #     mp.Process(target=spawn_validator, args=(CYCLE_HIGH_FREQUENCY,)),
    #     mp.Process(target=spawn_validator, args=(CYCLE_LOW_FREQUENCY,)),
    #     mp.Process(target=spawn_validator, args=(CYCLE_SCORING,)),
    # ]
    # for p in processes:
    #     p.start()
    # for p in processes:
    #     p.join()

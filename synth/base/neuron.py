# The MIT License (MIT)
# Copyright © 2023 Yuma Rao

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

import copy
import logging

import bittensor as bt
from tenacity import (
    before_log,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

from abc import ABC, abstractmethod

# Sync calls set weights and also resyncs the metagraph.
from synth.base.metagraph import Metagraph
from synth.base.synapse import Synapse
from synth.utils.config import check_config, add_args, config
from synth.utils.logging import setup_logging
from synth.utils.misc import ttl_get_block
from synth import __spec_version__ as spec_version

logger = logging.getLogger(__name__)


class BaseNeuron(ABC):
    """
    Base class for Bittensor miners. This class is abstract and should be inherited by a subclass. It contains the core logic for all neurons; validators and miners.

    In addition to creating a wallet, subtensor, and metagraph, this class also handles the synchronization of the network state via a basic checkpointing mechanism based on epoch length.
    """

    neuron_type: str = "BaseNeuron"

    @classmethod
    def check_config(cls, config):
        check_config(cls, config)

    @classmethod
    def add_args(cls, parser):
        add_args(cls, parser)

    @classmethod
    def config(cls):
        return config(cls)

    subtensor: bt.Subtensor
    wallet: bt.Wallet
    metagraph: Metagraph
    spec_version: int = spec_version

    @property
    def block(self):
        return ttl_get_block(self)

    def __init__(self, config=None):
        self.config = (  # type: ignore[method-assign]
            copy.deepcopy(config) if config is not None else self.config()
        )
        self.check_config(self.config)

        # Set up logging with the provided configuration.
        setup_logging(self.config.logging)

        # If a gpu is required, set the device to cuda:N (e.g. cuda:0)
        self.device = self.config.neuron.device

        # Log the configuration for reference.
        logger.info(self.config)

        # Build Bittensor objects
        # These are core Bittensor classes to interact with the network.
        logger.info("Setting up bittensor objects.")

        # The wallet holds the cryptographic key pairs for the miner.
        self.wallet = bt.Wallet(
            name=self.config.wallet.name,
            hotkey=self.config.wallet.hotkey,
            path=self.config.wallet.path,
        )
        self.subtensor = self._connect_subtensor()
        self.metagraph = self._initial_metagraph_fetch()

        logger.info(f"Wallet: {self.wallet}")
        logger.info(f"Subtensor: {self.subtensor}")
        logger.info(f"Metagraph: {self.metagraph}")

        # Check if the miner is registered on the Bittensor network before proceeding further.
        self.check_registered()

        # Each miner gets a unique identity (UID) in the network for differentiation.
        self.uid = self.metagraph.hotkeys.index(
            self.wallet.hotkey.ss58_address
        )
        logger.info(
            f"Running neuron on subnet: {self.config.netuid} with uid {self.uid} using network: {self.subtensor.endpoint}"
        )
        self.step = 0

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_random_exponential(multiplier=2, max=120),
        before=before_log(logger, logging.DEBUG),
        reraise=True,
    )
    def _connect_subtensor(self) -> bt.Subtensor:
        """Open the chain connection, retrying transient failures.

        bt.Subtensor() connects lazily; the first block read opens the
        websocket, so an unreachable endpoint raises here and is retried.
        """
        subtensor = bt.Subtensor(self.config.subtensor.network)
        logger.info(
            f"Connected to {subtensor.endpoint} at block {subtensor.block}"
        )
        return subtensor

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_random_exponential(multiplier=2, max=120),
        before=before_log(logger, logging.DEBUG),
        reraise=True,
    )
    def _initial_metagraph_fetch(self) -> Metagraph:
        return Metagraph(self.subtensor, self.config.netuid)

    @abstractmethod
    async def forward_miner(self, synapse: Synapse) -> Synapse: ...

    @abstractmethod
    def forward_validator(self): ...

    @abstractmethod
    def resync_metagraph(self): ...

    @abstractmethod
    def set_weights(self): ...

    @abstractmethod
    def run(self): ...

    def sync(self):
        """
        Wrapper for synchronizing the state of the network for the given miner or validator.
        """
        # Ensure miner or validator hotkey is still registered on the network.
        self.check_registered()

        if self.should_sync_metagraph():
            self.resync_metagraph()

        # we don't set_weights here because we control the set weights process
        # in the run() method. This to save to database the result of the set_weights.

        # Always save state.
        self.save_state()

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_random_exponential(multiplier=2, max=120),
        before=before_log(logger, logging.DEBUG),
        reraise=True,
    )
    def _is_hotkey_registered(self) -> bool:
        uid = self.subtensor.neurons.uid(
            self.wallet.hotkey.ss58_address, self.config.netuid
        )
        return uid is not None

    def check_registered(self):
        # --- Check for registration. Only the chain query is retried; an
        # answer of "not registered" is final and still exits.
        if not self._is_hotkey_registered():
            logger.error(
                f"Wallet: {self.wallet} is not registered on netuid {self.config.netuid}."
                f" Please register the hotkey using `btcli subnets register` before trying again"
            )
            exit()

    def should_sync_metagraph(self):
        """
        Check if enough epoch blocks have elapsed since the last checkpoint to sync.
        """
        return (
            self.block - self.metagraph.last_update[self.uid]
        ) > self.config.neuron.epoch_length

    def should_set_weights(self) -> bool:
        # Don't set weights on initialization.
        if self.step == 0:
            return False

        # Check if enough epoch blocks have elapsed since the last epoch.
        if self.config.neuron.disable_set_weights:
            return False

        # Define appropriate logic for when set weights.
        return (
            (self.block - self.metagraph.last_update[self.uid])
            > self.config.neuron.epoch_length
            and self.neuron_type != "MinerNeuron"
        )  # don't set weights if you're a miner

    def save_state(self):
        logger.warning(
            "save_state() not implemented for this neuron. You can implement this function to save model checkpoints or other useful data."
        )

    def load_state(self):
        logger.warning(
            "load_state() not implemented for this neuron. You can implement this function to load model checkpoints or other useful data."
        )

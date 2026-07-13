# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Opentensor Foundation

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

import os
import subprocess
import argparse
import bittensor as bt
from .logging import setup_events_logger
from synth.validator.storage_backend import (
    STORAGE_BACKEND_CHOICES,
    STORAGE_BACKEND_POSTGRES,
)


def is_cuda_available():
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "-L"], stderr=subprocess.STDOUT
        ).decode("utf-8")
        if "NVIDIA" in output:
            return "cuda"
    except Exception:
        pass
    try:
        output = subprocess.check_output(["nvcc", "--version"]).decode("utf-8")
        if "release" in output:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def check_config(_, config: "bt.Config"):
    r"""Checks/validates the config namespace object."""
    bt.logging.check_config(config)

    full_path = os.path.expanduser(
        "{}/{}/{}/netuid{}/{}".format(
            config.logging.logging_dir,  # TODO: change from ~/.bittensor/miners to ~/.bittensor/neurons
            config.wallet.name,
            config.wallet.hotkey,
            config.netuid,
            config.neuron.name,
        )
    )
    print("full path:", full_path)
    config.neuron.full_path = os.path.expanduser(full_path)
    if not os.path.exists(config.neuron.full_path):
        os.makedirs(config.neuron.full_path, exist_ok=True)

    if not config.neuron.dont_save_events:
        # Add custom event logger for the events.
        events_logger = setup_events_logger(
            config.neuron.full_path, config.neuron.events_retention_size
        )
        bt.logging.register_primary_logger(events_logger.name)


def add_args(_, parser):
    """
    Adds relevant arguments to the parser for operation.
    """

    parser.add_argument("--netuid", type=int, help="Subnet netuid", default=1)

    parser.add_argument(
        "--neuron.device",
        type=str,
        help="Device to run on.",
        default=is_cuda_available(),
    )

    parser.add_argument(
        "--neuron.epoch_length",
        type=int,
        help="The default epoch length (how often we set weights, measured in 12 second blocks).",
        default=100,
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help="Mock neuron and all network components.",
        default=False,
    )

    parser.add_argument(
        "--neuron.events_retention_size",
        type=str,
        help="Events retention size.",
        default=2 * 1024 * 1024 * 1024,  # 2 GB
    )

    parser.add_argument(
        "--neuron.dont_save_events",
        action="store_true",
        help="If set, we dont save events to a log file.",
        default=False,
    )


def add_miner_args(_, parser):
    """Add miner specific arguments to the parser."""

    parser.add_argument(
        "--neuron.name",
        type=str,
        help="Trials for this neuron go in neuron.root / (wallet_cold - wallet_hot) / neuron.name. ",
        default="miner",
    )

    parser.add_argument(
        "--blacklist.validator_min_stake",
        type=int,
        default=65000,
        help="Minimum validator stake to accept forward requests from as a miner",
    )

    parser.add_argument(
        "--wandb.enabled",
        type=bool,
        help="Boolean toggle for wandb integration",
        default=False,
    )

    parser.add_argument(
        "--wandb.project_name",
        type=str,
        default="template-miners",
        help="Wandb project to log to.",
    )

    parser.add_argument(
        "--wandb.entity",
        type=str,
        default="opentensor-dev",
        help="Wandb entity to log to.",
    )


def add_validator_args(_, parser: argparse.ArgumentParser):
    """Add validator specific arguments to the parser."""

    parser.add_argument(
        "--neuron.name",
        type=str,
        help="Trials for this neuron go in neuron.root / (wallet_cold - wallet_hot) / neuron.name. ",
        default="validator",
    )

    parser.add_argument(
        "--neuron.timeout",
        type=float,
        help="Override the timeout for each forward call in seconds.",
        default=None,
    )

    parser.add_argument(
        "--neuron.sample_size",
        type=int,
        help="The number of miners to query in a single step.",
        default=50,
    )

    parser.add_argument(
        "--neuron.disable_set_weights",
        action="store_true",
        help="Disables setting weights.",
        default=False,
    )

    parser.add_argument(
        "--neuron.vpermit_tao_limit",
        type=int,
        help="The maximum number of TAO allowed to query a validator with a vpermit.",
        default=999999,
    )

    parser.add_argument(
        "--gcp.log_id_prefix",
        type=str,
        help="The GCP log ID prefix.",
    )

    parser.add_argument(
        "--neuron.nprocs",
        type=int,
        help="The number of processes to run for the validator dendrite.",
        default=2,
    )

    parser.add_argument(
        "--validator.cycle_name",
        type=str,
        help="Low or high frequency or scoring cycle, start separate processes for each.",
        default="scoring",
    )

    parser.add_argument(
        "--validator.assets",
        type=str,
        help=(
            "Comma-separated list of assets to restrict the cycle to "
            "(e.g. 'BTC,ETH'). If unset, cycles through the full asset_list "
            "of the selected cycle. Only applies to low_frequency / "
            "high_frequency cycles; ignored for scoring."
        ),
        default="",
    )

    parser.add_argument(
        "--validator.cycle_offset_minutes",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--validator.mode",
        type=str,
        help="Full mode will keep all history, light mode will prune old predictions.",
        default="full",
    )

    parser.add_argument(
        "--retention.low.days",
        type=int,
        help="Data retention days for the low frequency prompt in light mode.",
        default=12,
    )

    parser.add_argument(
        "--retention.high.days",
        type=int,
        help="Data retention days for the high frequency prompt in light mode.",
        default=4,
    )

    parser.add_argument(
        "--cycle_interval_minutes.low",
        type=int,
        help="Cycle interval in minutes for the low frequency prompt.",
        default=6,
    )

    parser.add_argument(
        "--cycle_interval_minutes.high",
        type=int,
        help="Cycle interval in minutes for the high frequency prompt.",
        default=5,
    )
    parser.add_argument(
        "--storage.backend",
        type=str,
        choices=STORAGE_BACKEND_CHOICES,
        default=STORAGE_BACKEND_POSTGRES,
        help=(
            "Storage backend for miner prediction payloads. 'postgres' "
            "(default) stores predictions JSON. 'bigtable' offloads the "
            "prediction blob to Bigtable (configured via BIGTABLE_* env "
            "vars); see .env.example."
        ),
    )


def config(cls):
    """
    Returns the configuration object specific to this miner or validator after adding relevant arguments.
    """
    # bittensor 10.4.x disables CLI argument parsing unless BT_NO_PARSE_CLI_ARGS
    # is explicitly falsy (it defaults to "true"). Our neurons are configured
    # entirely through CLI flags, so without this bt.Config(parser) ignores
    # every --flag and returns only DEFAULTS (config.neuron is None, subtensor
    # on finney, etc.). Scoped here (not module level) so it only affects
    # neuron config building, never bittensor's logging machine under pytest.
    os.environ.setdefault("BT_NO_PARSE_CLI_ARGS", "false")
    parser = argparse.ArgumentParser()
    bt.Wallet.add_args(parser)
    bt.Subtensor.add_args(parser)
    bt.logging.add_args(parser)
    bt.Axon.add_args(parser)
    cls.add_args(parser)
    return bt.Config(parser)

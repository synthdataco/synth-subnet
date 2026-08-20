# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Mode Labs

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

from datetime import datetime, timedelta
import random
import time
import sys
import traceback

import bittensor as bt
import numpy as np


from synth.base.dendrite_multiprocess import sync_forward_multiprocess
from synth.base.validator import BaseValidatorNeuron
from synth.protocol import Simulation
from synth.simulation_input import SimulationInput
from synth.utils.helpers import (
    get_current_time,
    timeout_from_start_time,
    convert_list_elements_to_str,
)
from synth.utils.logging import print_execution_time
from synth.utils.uids import check_uid_availability
from synth.validator import competition_config
from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.prediction_notifier import PredictionNotifier
from synth.validator.moving_average import (
    combine_moving_averages,
    compute_smoothed_score,
    compute_vhft_smoothed_score,
    prepare_df_for_moving_average,
    print_rewards_df,
)
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator.response_validation_v2 import (
    validate_responses as validate_responses_v2,
)
from synth.validator.reward import (
    get_rewards_multiprocess,
    print_scores_df,
)


@print_execution_time
def send_weights_to_bittensor_and_update_weights_history(
    base_neuron: BaseValidatorNeuron,
    moving_averages_data: list[dict],
    miner_data_handler: MinerDataHandler,
    scored_time: datetime,
):
    miner_weights = [item["reward_weight"] for item in moving_averages_data]
    miner_uids = [item["miner_uid"] for item in moving_averages_data]

    base_neuron.update_scores(np.array(miner_weights), miner_uids)

    base_neuron.sync()
    base_neuron.resync_metagraph()
    result, msg, uint_uids, uint_weights = base_neuron.set_weights()

    if result:
        bt.logging.success("set_weights on chain successfully!")
        msg = "SUCCESS"
    else:
        bt.logging.warning(msg, "set_weights failed")

    miner_data_handler.update_weights_history(
        miner_uids=miner_uids,
        miner_weights=miner_weights,
        norm_miner_uids=convert_list_elements_to_str(uint_uids),
        norm_miner_weights=convert_list_elements_to_str(uint_weights),
        update_result=msg,
        scored_time=scored_time,
    )


@print_execution_time
def calculate_moving_average_and_update_rewards(
    miner_data_handler: MinerDataHandler,
    scored_time: datetime,
    vhft_provider=None,
) -> list[dict]:
    moving_averages_data: dict[str, list[dict]] = {}
    for comp in competition_config.ALL_COMPETITIONS:
        miner_scores_df = miner_data_handler.get_miner_scores(
            scored_time,
            comp.window_days,
            comp.time_length,
            comp.asset_list,
        )

        if miner_scores_df.empty:
            continue

        df = prepare_df_for_moving_average(miner_scores_df)

        moving_averages = compute_smoothed_score(
            miner_data_handler,
            df,
            scored_time,
            comp,
        )

        print_rewards_df(moving_averages, comp.label)

        if moving_averages is None or len(moving_averages) == 0:
            continue

        miner_data_handler.update_miner_rewards(moving_averages)
        moving_averages_data[comp.label] = moving_averages

    # VHFT: a 4th competition scored OFF-subnet. Pulled from the external scorer and
    # run through the SAME shape+blend tail as the others. Off (skipped) unless
    # VHFT_SCORES_URL is set (vhft_provider is None), so this is inert until enabled.
    if vhft_provider is not None:
        vhft_scores = vhft_provider.fetch_scores()
        if vhft_scores:
            vhft_ma = compute_vhft_smoothed_score(
                miner_data_handler,
                vhft_scores,
                scored_time,
                competition_config.VHFT_COMPETITION,
            )
            if vhft_ma:
                miner_data_handler.update_miner_rewards(vhft_ma)
                moving_averages_data[
                    competition_config.VHFT_COMPETITION.label
                ] = vhft_ma

    return combine_moving_averages(moving_averages_data)


@print_execution_time
def calculate_scores(
    miner_data_handler: MinerDataHandler,
    price_data_provider: PriceDataProvider,
    scored_time: datetime,
    comp: competition_config.CompetitionConfig,
    nprocs: int = 2,
) -> bool:
    # get latest prediction request from validator
    validator_requests = miner_data_handler.get_validator_requests_to_score(
        scored_time, comp.window_days, comp.time_length, comp.asset_list
    )

    if validator_requests is None or len(validator_requests) == 0:
        bt.logging.warning("No prediction requests found")
        return False

    bt.logging.debug(f"found {len(validator_requests)} prediction requests")

    fail_count = 0
    for validator_request in validator_requests:
        bt.logging.debug(f"validator_request_id: {validator_request.id}")

        prompt_scores, detailed_info, real_prices = get_rewards_multiprocess(
            miner_data_handler=miner_data_handler,
            price_data_provider=price_data_provider,
            validator_request=validator_request,
            comp=comp,
            nprocs=nprocs,
        )

        print_scores_df(prompt_scores, detailed_info)

        if prompt_scores is None:
            bt.logging.warning("No rewards calculated")
            fail_count += 1
            continue

        miner_score_time = validator_request.start_time + timedelta(
            seconds=int(validator_request.time_length)
        )

        miner_data_handler.set_miner_scores(
            real_prices,
            int(validator_request.id),
            detailed_info,
            miner_score_time,
        )

    # Success if at least one request succeed
    return fail_count != len(validator_requests)


@print_execution_time
def query_available_miners_and_save_responses(
    base_neuron: BaseValidatorNeuron,
    miner_data_handler: MinerDataHandler,
    miner_uids: list,
    simulation_input: SimulationInput,
    request_time: datetime,
    prediction_notifier: PredictionNotifier | None = None,
):
    timeout = timeout_from_start_time(simulation_input.start_time)

    # synapse - is a message that validator sends to miner to get results, i.e. simulation_input in our case
    # Simulation - is our protocol, i.e. input and output message of a miner (application that returns prediction of
    # prices for a chosen asset)
    synapse = Simulation(simulation_input=simulation_input)
    # The dendrite client queries the network:
    # it is the actual call to all the miners from validator
    # returns an array of synapses (predictions) for each of the miners
    # ======================================================
    # miner has a unique uuid in the subnetwork
    # ======================================================
    # axon is a server application that accepts requests on the miner side
    # ======================================================

    axons = [base_neuron.metagraph.axons[uid] for uid in miner_uids]

    start_time = time.time()

    # synapses = await base_neuron.dendrite.forward(
    #     axons=axons,
    #     synapse=synapse,
    #     client=client,
    #     timeout=timeout,
    # )

    synapses = sync_forward_multiprocess(
        base_neuron.dendrite.keypair,
        base_neuron.dendrite.uuid,
        base_neuron.dendrite.external_ip,
        axons,
        synapse,
        timeout,
        base_neuron.config.neuron.nprocs,
    )

    total_process_time = str(time.time() - start_time)
    bt.logging.debug(
        f"Forwarding took {total_process_time} seconds",
        "base_neuron.dendrite.forward",
    )

    miner_predictions = {}
    for i, synapse in enumerate(synapses):
        response = synapse.deserialize()
        process_time = synapse.dendrite.process_time
        try:
            format_validation = validate_responses_v2(
                response, simulation_input, process_time
            )
        except Exception:
            format_validation = "error during validation"
            traceback.print_exc(file=sys.stderr)
        miner_id = miner_uids[i]
        miner_predictions[miner_id] = (
            response,
            format_validation,
            process_time,
        )

    if len(miner_predictions) > 0:
        validator_requests_id = miner_data_handler.save_responses(
            miner_predictions,
            simulation_input,
            request_time,
        )
        if (
            validator_requests_id is not None
            and prediction_notifier is not None
        ):
            prediction_notifier.publish_stored(
                validator_request_id=validator_requests_id,
                simulation_input=simulation_input,
            )
    else:
        bt.logging.info("skip saving because no prediction")


@print_execution_time
def get_available_miners_and_update_metagraph_history(
    base_neuron: BaseValidatorNeuron,
    miner_data_handler: MinerDataHandler,
    save_snapshot: bool,
):
    # Sync metagraph to get latest miner addresses
    base_neuron.metagraph.sync(subtensor=base_neuron.subtensor)

    start_time = get_current_time()
    miner_uids = []
    miners = []
    metagraph_info = []
    for uid in range(len(base_neuron.metagraph.S)):
        uid_is_available = check_uid_availability(
            base_neuron.metagraph,
            uid,
            base_neuron.config.neuron.vpermit_tao_limit,
        )

        # adding the uid even if not available, to generate a score
        miner_uids.append(uid)
        miners.append(
            {
                "neuron_uid": uid,
                "coldkey": base_neuron.metagraph.coldkeys[uid],
                "hotkey": base_neuron.metagraph.hotkeys[uid],
            }
        )

        if uid_is_available:
            metagraph_item = {
                "neuron_uid": uid,
                "incentive": float(base_neuron.metagraph.I[uid]),
                "rank": 0.0,
                "stake": float(base_neuron.metagraph.S[uid]),
                "trust": 0.0,
                "emission": float(base_neuron.metagraph.E[uid]),
                "pruning_score": 0.0,
                "coldkey": base_neuron.metagraph.coldkeys[uid],
                "hotkey": base_neuron.metagraph.hotkeys[uid],
                "updated_at": start_time.isoformat(),
                "ip_address": base_neuron.metagraph.addresses[uid],
            }
            metagraph_info.append(metagraph_item)

    # Always upsert miner identities: save_responses maps uid -> miner_id
    # through the miners table, so a new/re-registered uid must have its row
    # before its first queried predictions are saved (else they are dropped
    # or attributed to the uid's previous identity). The on-conflict
    # updated_at bump is load-bearing: it keeps a re-current identity the
    # newest row for its uid.
    if len(miners) > 0:
        miner_data_handler.insert_new_miners(miners)

    # The metagraph_history snapshot is appended on the caller's schedule,
    # since downstream analytics read it hourly.
    if len(metagraph_info) > 0 and save_snapshot:
        miner_data_handler.update_metagraph_history(metagraph_info)

    random.shuffle(miner_uids)

    return miner_uids

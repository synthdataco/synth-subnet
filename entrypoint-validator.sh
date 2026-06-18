#!/bin/bash

default_network=finney
network="${NETWORK:-$default_network}"
mainnet_netuid=50
netuid=${NETUID:-$mainnet_netuid}

vpermit_tao_limit=999999

default_validator_coldkey_name=validator
validator_coldkey_name="${VALIDATOR_COLDKEY_NAME:-$default_validator_coldkey_name}"

default_validator_hotkey_name=default
validator_hotkey_name="${VALIDATOR_HOTKEY_NAME:-$default_validator_hotkey_name}"

default_log_id_prefix=my_validator_name
log_id_prefix="${LOG_ID_PREFIX:-$default_log_id_prefix}"

python3.11 ./neurons/validator.py \
		--wallet.name $validator_coldkey_name \
		--wallet.hotkey $validator_hotkey_name \
		--subtensor.network $network \
		--netuid $netuid \
		--logging.debug \
		--neuron.vpermit_tao_limit $vpermit_tao_limit \
		--gcp.log_id_prefix $log_id_prefix \
		--neuron.nprocs 8 \
		--validator.cycle_name high_frequency &
PID1=$!

python3.11 ./neurons/validator.py \
		--wallet.name $validator_coldkey_name \
		--wallet.hotkey $validator_hotkey_name \
		--subtensor.network $network \
		--netuid $netuid \
		--logging.debug \
		--neuron.vpermit_tao_limit $vpermit_tao_limit \
		--gcp.log_id_prefix $log_id_prefix \
		--neuron.nprocs 8 \
		--validator.cycle_name low_frequency &
PID2=$!

python3.11 ./neurons/validator.py \
		--wallet.name $validator_coldkey_name \
		--wallet.hotkey $validator_hotkey_name \
		--subtensor.network $network \
		--netuid $netuid \
		--logging.debug \
		--neuron.vpermit_tao_limit $vpermit_tao_limit \
		--gcp.log_id_prefix $log_id_prefix \
		--neuron.nprocs 8 \
		--validator.cycle_name scoring &
PID3=$!

wait $PID1 $PID2 $PID3

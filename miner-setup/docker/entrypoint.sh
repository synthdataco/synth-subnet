#!/bin/bash
set -euo pipefail

WALLET_NAME="${WALLET_NAME:-miner}"
WALLET_HOTKEY="${WALLET_HOTKEY:-default}"
NETWORK="${NETWORK:-finney}"
NETUID="${NETUID:-50}"
AXON_PORT="${AXON_PORT:-8091}"
VALIDATOR_MIN_STAKE="${VALIDATOR_MIN_STAKE:-65000}"

echo "Starting synth-subnet miner"
echo "  Wallet:  $WALLET_NAME / $WALLET_HOTKEY"
echo "  Network: $NETWORK (netuid $NETUID)"
echo "  Port:    $AXON_PORT"

python neurons/miner.py \
    --wallet.name "$WALLET_NAME" \
    --wallet.hotkey "$WALLET_HOTKEY" \
    --subtensor.network "$NETWORK" \
    --netuid "$NETUID" \
    --axon.port "$AXON_PORT" \
    --blacklist.validator_min_stake "$VALIDATOR_MIN_STAKE" \
    --logging.debug

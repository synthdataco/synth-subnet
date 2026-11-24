import logging
import argparse
import bittensor as bt
import asyncio
import json

logger = logging.getLogger(__name__)


async def main(
    wallet_name: str, hotkey_name: str, hyperparam: str, body: dict
):
    wallet = bt.Wallet(name=wallet_name, hotkey=hotkey_name)
    subtensor = bt.Subtensor("finney")
    call = getattr(bt.calls.AdminUtils, hyperparam)(netuid=50, **body)
    result = subtensor.submit_call(call, wallet)
    if result.success:
        logger.info(f"Set successful {result.success}")
    else:
        logger.error(f"Set failed: {result.message}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bittensor Admin Utils Script"
    )
    parser.add_argument(
        "--wallet_name", type=str, required=True, help="Name of the wallet"
    )
    parser.add_argument(
        "--hotkey_name", type=str, required=True, help="Name of the hotkey"
    )
    parser.add_argument(
        "--hyperparam", type=str, required=True, help="Hyperparameter to set"
    )
    parser.add_argument(
        "--body",
        type=json.loads,
        required=True,
        help="JSON string of call parameters (excluding netuid)",
    )
    args = parser.parse_args()
    asyncio.run(
        main(args.wallet_name, args.hotkey_name, args.hyperparam, args.body)
    )

# Example usage:
# python verify/hyperparameters.py --wallet_name owner --hotkey_name default --hyperparam sudo_set_min_burn --body '{"min_burn": 500000000}'

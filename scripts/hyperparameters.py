import argparse
import bittensor as bt
import asyncio
import json


async def main(
    wallet_name: str, hotkey_name: str, hyperparam: str, body: dict
):
    wallet = bt.Wallet(name=wallet_name, hotkey=hotkey_name)
    subtensor = bt.Subtensor(network="finney")
    call = subtensor.substrate.compose_call(
        call_module="AdminUtils",
        call_function=hyperparam,
        call_params={"netuid": 50, **body},
    )
    success, err_msg = subtensor.sign_and_send_extrinsic(call, wallet)
    if success:
        bt.logging.success(f"Set successful {success}")
    else:
        bt.logging.error(f"Set failed: {err_msg}")


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

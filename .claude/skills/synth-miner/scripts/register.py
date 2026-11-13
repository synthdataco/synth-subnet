# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["bittensor==10.4.1", "bittensor-wallet==4.1.0"]
# [tool.uv]
# exclude-newer = "2026-11-01T00:00:00Z"
# ///
"""Register a miner hotkey on a subnet, signed by the coldkey alone.

Reads the coldkey password from a file (first line), unlocks the coldkey in
this process and signs SubtensorModule.register_limit for the hotkey's SS58
address with limit_price = --max-tao, so the chain refuses the registration
if the price rises above it. Only public data is printed: SS58 addresses,
balances, the UID, the axon and the extrinsic hash. Never prompts.

Run it with uv, which installs the pinned dependencies above:

    uv run --script register.py --wallet-name miner --check
    uv run --script register.py --wallet-name miner --max-tao 0.2 --dry-run
    uv run --script register.py --wallet-name miner --max-tao 0.2

--check reads the registration state, price and balance and needs no
password; for a registered hotkey it also reports the axon published on
chain. --dry-run unlocks the coldkey and builds and signs the extrinsic
without submitting it. A hotkey that already holds a UID on the subnet is
reported as registered without reading the password, so a re-run with the
same hotkey never registers twice. The price and balance checks run before
the password file is read.

The password file must be a regular file owned by the current user with no
group or other access (mode 0600 or stricter). An unencrypted coldkey needs
no password file.

Stdout carries one JSON object; logs go to stderr. On failure the object
has "error" (a short name, or the chain error name) and "message".

Exit codes:
    0   registered, already registered, --check report, or a --dry-run
        with nothing blocking the registration
    1   unexpected error (traceback on stderr, nothing on stdout)
    2   usage error, or the subnet does not exist
    3   wallet file missing or unusable (coldkeypub.txt, hotkey, coldkey),
        or the hotkey is encrypted
    4   the hotkey belongs to another coldkey
    5   the live price is above --max-tao
    6   the coldkey balance is below the price plus the transaction fee
    7   password file missing, empty, not a regular file, not owned by the
        current user, or readable or writable by group or others
    8   the password does not decrypt the coldkey
    9   the chain rejected the extrinsic ("error" holds the chain error name)
    10  the extrinsic succeeded but the hotkey has no UID after 5 blocks
--dry-run signs even when the price or balance check fails, then exits with
5 or 6.
"""

import argparse
import json
import os
import stat
import sys

from bittensor.core.errors import (
    SubstrateRequestException,
    chain_error_from_substrate_exception,
)
from bittensor.core.subtensor import Subtensor
from bittensor.utils import format_error_message
from bittensor.utils.balance import Balance
from bittensor_wallet import Keypair, Wallet
from bittensor_wallet.errors import KeyFileError

ERA_PERIOD = 64
CONFIRM_BLOCKS = 5


class Refusal(Exception):
    def __init__(self, code: int, error: str, message: str):
        super().__init__(message)
        self.code = code
        self.error = error
        self.message = message


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog=__doc__[__doc__.index("Exit codes:") :],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--wallet-name", required=True)
    parser.add_argument("--hotkey-name", default="default")
    parser.add_argument("--wallet-path", default="~/.bittensor/wallets")
    parser.add_argument("--network", default="finney")
    parser.add_argument("--netuid", type=int, default=50)
    parser.add_argument(
        "--password-file", default="~/.synth-miner/coldkey-password"
    )
    parser.add_argument(
        "--max-tao",
        type=float,
        help="highest registration price to pay; required unless --check",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="report registration state, price and balance, then exit",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="unlock and sign the extrinsic without submitting it",
    )
    args = parser.parse_args()
    if args.max_tao is None and not args.check:
        parser.error("--max-tao is required unless --check is given")
    return args


def coldkey_ss58(wallet: Wallet) -> str:
    if not wallet.coldkeypub_file.exists_on_device():
        raise Refusal(3, "WalletFiles", f"no {wallet.coldkeypub_file.path}")
    return wallet.coldkeypub.ss58_address


def hotkey_ss58(wallet: Wallet) -> str:
    """The hotkey's address, from the private hotkey file when present
    (checked against its pub file), else from the pub file alone."""
    if not wallet.hotkey_file.exists_on_device():
        if wallet.hotkeypub_file.exists_on_device():
            return wallet.hotkeypub.ss58_address
        raise Refusal(3, "WalletFiles", f"no {wallet.hotkey_file.path}")
    if wallet.hotkey_file.is_encrypted():
        raise Refusal(
            3,
            "WalletFiles",
            f"{wallet.hotkey_file.path} is encrypted; the miner needs an"
            " unencrypted hotkey",
        )
    address = wallet.get_hotkey().ss58_address
    if (
        wallet.hotkeypub_file.exists_on_device()
        and wallet.hotkeypub.ss58_address != address
    ):
        raise Refusal(
            3,
            "WalletFiles",
            f"{wallet.hotkeypub_file.path} does not match"
            f" {wallet.hotkey_file.path}",
        )
    return address


def read_password(path: str) -> str:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise Refusal(7, "PasswordFile", f"{path} does not exist") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refusal(7, "PasswordFile", f"{path} is not a regular file")
    if info.st_uid != os.getuid():
        raise Refusal(
            7, "PasswordFile", f"{path} is not owned by the current user"
        )
    if info.st_mode & 0o077:
        raise Refusal(
            7,
            "PasswordFile",
            f"{path} has mode {stat.S_IMODE(info.st_mode):04o};"
            f" run: chmod 600 {path}",
        )
    with open(path, encoding="utf-8") as handle:
        password = handle.readline().rstrip("\r\n")
    if not password:
        raise Refusal(7, "PasswordFile", f"{path} is empty")
    return password


def unlock_coldkey(wallet: Wallet, password_file: str) -> Keypair:
    if not wallet.coldkey_file.exists_on_device():
        raise Refusal(3, "WalletFiles", f"no {wallet.coldkey_file.path}")
    if not wallet.coldkey_file.is_encrypted():
        log("warning: the coldkey is not encrypted; no password file used")
        return wallet.get_coldkey()
    password = read_password(password_file)
    try:
        return wallet.get_coldkey(password=password)
    except KeyFileError:
        raise Refusal(
            8,
            "WrongPassword",
            f"the password in {password_file} does not decrypt"
            f" {wallet.coldkey_file.path}",
        ) from None
    finally:
        del password


def read_state(
    subtensor: Subtensor, args: argparse.Namespace, result: dict
) -> None:
    owner = subtensor.get_hotkey_owner(result["hotkey"])
    if owner is not None and owner != result["coldkey"]:
        raise Refusal(
            4,
            "HotkeyOwnedByAnotherColdkey",
            f"hotkey {result['hotkey']} belongs to coldkey {owner}",
        )
    uid = subtensor.get_uid_for_hotkey_on_subnet(result["hotkey"], args.netuid)
    price = subtensor.recycle(args.netuid)
    if price is None:
        raise Refusal(2, "SubnetNotFound", f"no subnet {args.netuid}")
    result.update(
        registered=uid is not None,
        uid=uid,
        price_tao=price.tao,
        balance_tao=subtensor.get_balance(result["coldkey"]).tao,
    )
    if uid is not None:
        axon = subtensor.get_neuron_for_pubkey_and_subnet(
            result["hotkey"], args.netuid
        ).axon_info
        result["axon"] = f"{axon.ip}:{axon.port}" if axon.is_serving else None


def check_funds(fee: Balance, args: argparse.Namespace, result: dict) -> None:
    price, balance = result["price_tao"], result["balance_tao"]
    if price > args.max_tao:
        raise Refusal(
            5,
            "PriceAboveMax",
            f"price {price} TAO is above --max-tao {args.max_tao}",
        )
    if balance < price + fee.tao:
        raise Refusal(
            6,
            "InsufficientBalance",
            f"balance {balance} TAO is below the price {price} TAO plus"
            f" the fee {fee.tao} TAO",
        )
    if balance < args.max_tao + fee.tao:
        log(
            f"warning: balance {balance} TAO does not cover --max-tao"
            f" {args.max_tao} TAO plus the fee {fee.tao} TAO"
        )


def submit(subtensor: Subtensor, extrinsic, result: dict) -> None:
    try:
        receipt = subtensor.substrate.submit_extrinsic(
            extrinsic, wait_for_inclusion=True, wait_for_finalization=True
        )
    except SubstrateRequestException as exc:
        typed = chain_error_from_substrate_exception(exc)
        raise Refusal(
            9,
            type(typed or exc).__name__,
            format_error_message(exc),
        ) from None
    if not receipt.is_success:
        error = receipt.error_message or {}
        raise Refusal(
            9,
            error.get("name", "ExtrinsicFailed"),
            format_error_message(error),
        )
    result["extrinsic_id"] = receipt.get_extrinsic_identifier()
    result["block"] = receipt.block_number


def confirm_uid(
    subtensor: Subtensor, args: argparse.Namespace, result: dict
) -> None:
    for _ in range(CONFIRM_BLOCKS):
        uid = subtensor.get_uid_for_hotkey_on_subnet(
            result["hotkey"], args.netuid
        )
        if uid is not None:
            result.update(registered=True, uid=uid)
            return
        subtensor.wait_for_block()
    raise Refusal(
        10,
        "NotConfirmed",
        f"hotkey {result['hotkey']} has no UID on subnet {args.netuid}"
        f" after {CONFIRM_BLOCKS} blocks",
    )


def register(
    subtensor: Subtensor,
    wallet: Wallet,
    args: argparse.Namespace,
    result: dict,
) -> int:
    call = subtensor.compose_call(
        "SubtensorModule",
        "register_limit",
        {
            "netuid": args.netuid,
            "hotkey": result["hotkey"],
            "limit_price": Balance.from_tao(args.max_tao).rao,
        },
    )
    fee = subtensor.get_extrinsic_fee(call=call, keypair=wallet.coldkeypub)
    result.update(max_tao=args.max_tao, fee_tao=fee.tao)
    blocked = None
    try:
        check_funds(fee, args, result)
    except Refusal as refusal:
        if not args.dry_run:
            raise
        log(f"dry run: a real run would stop here: {refusal.message}")
        blocked = refusal
    keypair = unlock_coldkey(wallet, os.path.expanduser(args.password_file))
    extrinsic = subtensor.substrate.create_signed_extrinsic(
        call=call, keypair=keypair, era={"period": ERA_PERIOD}
    )
    del keypair
    result["extrinsic"] = "0x" + extrinsic.extrinsic_hash.hex()
    if args.dry_run:
        log("dry run: signed, not submitted")
        result.update(dry_run=True, submitted=False)
        if blocked is None:
            return 0
        raise blocked
    submit(subtensor, extrinsic, result)
    confirm_uid(subtensor, args, result)
    return 0


def run(args: argparse.Namespace, result: dict) -> int:
    wallet = Wallet(
        name=args.wallet_name,
        hotkey=args.hotkey_name,
        path=os.path.expanduser(args.wallet_path),
    )
    result.update(coldkey=coldkey_ss58(wallet), hotkey=hotkey_ss58(wallet))
    with Subtensor(network=args.network) as subtensor:
        read_state(subtensor, args, result)
        if args.check or result["registered"]:
            return 0
        return register(subtensor, wallet, args, result)


def main() -> int:
    args = parse_args()
    # Keep fd 1 for the JSON result: the wallet library prints to stdout.
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    result: dict = {"network": args.network, "netuid": args.netuid}
    try:
        code = run(args, result)
    except Refusal as refusal:
        log(f"error: {refusal.message}")
        result.update(error=refusal.error, message=refusal.message)
        code = refusal.code
    print(json.dumps(result), file=out, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())

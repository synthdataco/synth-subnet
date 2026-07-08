"""Set up (or clear) your public miner profile metadata.

Signs a metadata payload (display name, avatar, social handles) with your
coldkey — the sr25519 signature is the proof of ownership, no account
needed — and submits it to the miner dashboard API. The metadata is
rendered on your public miner profile page.

Run this one-shot script on whatever machine holds your coldkey (it never
leaves the machine; only the signature is sent). Each submission replaces
all five fields: an omitted field is cleared. Passing --delete removes
your metadata entirely.

Field constraints (enforced server-side):
- display name: at most 40 printable characters
- twitter / discord: bare handles (no URL), at most 64 characters from
  letters, digits, ``_ . - #``
- avatar / linkedin: https:// URLs of at most 300 characters; the
  linkedin URL must be on linkedin.com
"""

import argparse
import hashlib
import json
import time

import bittensor
import requests

# To skip the interactive decrypt prompt, set WALLET_PASS in your environment, e.g.:
#   export WALLET_PASS='...'
# Avoid hardcoding wallet passwords in this script.


def canonical_payload_hash(payload: dict) -> str:
    canonical = json.dumps(
        {key: value for key, value in payload.items() if value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def main(args):
    if args.delete:
        payload = {
            "display_name": "",
            "avatar_url": "",
            "twitter": "",
            "discord": "",
            "linkedin": "",
        }
    else:
        payload = {
            "display_name": args.display_name or "",
            "avatar_url": args.avatar_url or "",
            "twitter": args.twitter or "",
            "discord": args.discord or "",
            "linkedin": args.linkedin or "",
        }
    if not args.delete and not any(payload.values()):
        raise SystemExit(
            "Refusing to submit empty metadata. Provide at least one field or use --delete."
        )
    if args.wallet_path:
        wallet = bittensor.Wallet(name=args.wallet_name, path=args.wallet_path)
    else:
        wallet = bittensor.Wallet(name=args.wallet_name)
    keypair = wallet.coldkey

    timestamp = int(time.time())
    message = (
        f"synth-profile-metadata|v1|{keypair.ss58_address}"
        f"|{timestamp}|{canonical_payload_hash(payload)}"
    )
    signature = keypair.sign(data=message)

    response = requests.put(
        f"{args.api_url}/v1/profiles/{keypair.ss58_address}/metadata",
        json={
            "payload": payload,
            "timestamp": timestamp,
            "signature": signature.hex(),
        },
        timeout=30,
    )
    print(f"{response.status_code} {response.text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sign and submit your public miner profile metadata"
    )
    parser.add_argument(
        "--wallet_name", type=str, required=True, help="Name of the wallet"
    )
    parser.add_argument(
        "--wallet_path",
        type=str,
        help="Path to the wallets directory (default ~/.bittensor/wallets)",
    )
    parser.add_argument("--display-name", type=str, help="Public display name")
    parser.add_argument(
        "--avatar-url", type=str, help="https:// URL of your avatar image"
    )
    parser.add_argument("--twitter", type=str, help="Twitter/X handle")
    parser.add_argument("--discord", type=str, help="Discord handle")
    parser.add_argument(
        "--linkedin", type=str, help="https://www.linkedin.com/... URL"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Remove your profile metadata entirely",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="https://monitoring.synthdata.co",
        help="Miner dashboard API base URL",
    )
    args = parser.parse_args()

    main(args)

# Example usage:
# python scripts/submit_miner_profile.py --wallet_name my_wallet --display-name "My Mining Co" --avatar-url https://example.com/logo.png --twitter my_handle

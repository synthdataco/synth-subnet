"""Keep the miner axon port's UFW allowlist in sync with honest validators.

This script reconciles the per-IP `ufw` rules guarding the miner axon port
with the set of honest validators currently published on the Bittensor
metagraph. A validator is admitted only when it holds the validator permit
and stakes at least `--blacklist.validator_min_stake` (default 65000), so the
firewall mirrors the miner's own blacklist predicate (neurons/miner.py).
On each cycle it adds rules for newly-seen validator IPs, removes rules for
IPs that no longer qualify, and retires any blanket `ALLOW Anywhere` rule once
a non-empty per-IP allowlist is in place — never leaving the port unreachable.

By default it runs the reconciliation in a loop every
`--chain_sync.interval_seconds` and mutates UFW (requires root with ufw
installed). Pass `--dry-run` to only read the metagraph and print the desired
allowlist without reading or touching UFW — use it to preview which validator
IPs would be allowed before applying any changes.
"""

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List, Set

import bittensor as bt


@dataclass(frozen=True)
class UfwState:
    current: Set[str]
    has_blanket: bool


@dataclass(frozen=True)
class UfwDiff:
    to_add: List[str]
    to_remove: List[str]
    remove_blanket: bool


def compute_desired_allowlist(metagraph, min_stake: int) -> Set[str]:
    """Mirrors the miner blacklist predicate at neurons/miner.py:108-117."""
    desired: Set[str] = set()
    for uid in range(int(metagraph.n)):
        if not bool(metagraph.validator_permit[uid]):
            continue
        if float(metagraph.S[uid]) < float(min_stake):
            continue
        ip = str(metagraph.axons[uid].ip)
        if ip and ip != "0.0.0.0":
            print(
                f"Adding {ip} to desired allowlist for uid {uid} with stake {metagraph.S[uid]}"
            )
            desired.add(ip)
    return desired


# UFW `status` lines we care about look like:
#   "8091/tcp                   ALLOW       Anywhere"
#   "8091/tcp                   ALLOW       1.2.3.4"
# The "(v6)" duplicates are emitted on dual-stack hosts; we ignore them.
_STATUS_LINE = re.compile(
    r"^\s*(?P<dst>\S+)\s+ALLOW(?:\s+IN)?\s+(?P<src>\S+)\s*$"
)


def parse_ufw_status(stdout: str, axon_port: int) -> UfwState:
    port_tcp = f"{axon_port}/tcp"
    port_plain = str(axon_port)
    current: Set[str] = set()
    has_blanket = False
    for raw in stdout.splitlines():
        if "(v6)" in raw:
            continue
        m = _STATUS_LINE.match(raw)
        if not m:
            continue
        dst = m.group("dst")
        src = m.group("src")
        if dst not in (port_tcp, port_plain):
            continue
        if src == "Anywhere":
            has_blanket = True
        else:
            current.add(src)
    return UfwState(current=current, has_blanket=has_blanket)


def compute_diff(
    desired: Set[str], state: UfwState, sort_for_determinism: bool = True
) -> UfwDiff:
    add = desired - state.current
    remove = state.current - desired
    to_add = sorted(add) if sort_for_determinism else list(add)
    to_remove = sorted(remove) if sort_for_determinism else list(remove)
    # Only retire the blanket rule once we have a non-empty replacement
    # allowlist actually in place — never leave the port unreachable.
    remove_blanket = state.has_blanket and len(desired) > 0
    return UfwDiff(
        to_add=to_add, to_remove=to_remove, remove_blanket=remove_blanket
    )


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def read_ufw_status(axon_port: int) -> UfwState:
    proc = _run(["ufw", "status"])
    if proc.returncode != 0:
        raise RuntimeError(
            f"`ufw status` failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return parse_ufw_status(proc.stdout, axon_port)


def apply_diff(diff: UfwDiff, axon_port: int) -> None:
    for ip in diff.to_add:
        proc = _run(
            [
                "ufw",
                "allow",
                "proto",
                "tcp",
                "from",
                ip,
                "to",
                "any",
                "port",
                str(axon_port),
            ]
        )
        if proc.returncode != 0:
            bt.logging.error(f"ufw allow {ip} failed: {proc.stderr.strip()}")
    for ip in diff.to_remove:
        proc = _run(
            [
                "ufw",
                "delete",
                "allow",
                "proto",
                "tcp",
                "from",
                ip,
                "to",
                "any",
                "port",
                str(axon_port),
            ]
        )
        if proc.returncode != 0:
            bt.logging.error(f"ufw delete {ip} failed: {proc.stderr.strip()}")
    if diff.remove_blanket:
        proc = _run(["ufw", "delete", "allow", f"{axon_port}/tcp"])
        if proc.returncode != 0:
            bt.logging.error(
                "ufw delete blanket rule failed: " f"{proc.stderr.strip()}"
            )


def reconcile_once(
    subtensor: "bt.Subtensor",
    netuid: int,
    axon_port: int,
    min_stake: int,
) -> None:
    metagraph = subtensor.metagraph(netuid)
    desired = compute_desired_allowlist(metagraph, min_stake)
    if not desired:
        # Treat an empty desired set as a sync glitch, not "nuke everything".
        bt.logging.warning(
            "desired allowlist is empty, skipping reconciliation"
        )
        return

    state = read_ufw_status(axon_port)
    if state.has_blanket:
        bt.logging.warning(
            f"blanket `{axon_port}/tcp ALLOW Anywhere` rule present; "
            "will be removed once per-IP rules are applied"
        )

    diff = compute_diff(desired, state)
    apply_diff(diff, axon_port)
    bt.logging.info(
        f"chain-sync cycle: desired={len(desired)} "
        f"current={len(state.current)} added={len(diff.to_add)} "
        f"removed={len(diff.to_remove)} "
        f"blanket_removed={diff.remove_blanket}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "UFW chain-sync agent: keeps the miner axon port's UFW "
            "allowlist in sync with current validator IPs from the "
            "Bittensor metagraph."
        )
    )
    parser.add_argument("--netuid", type=int, default=50)
    parser.add_argument(
        "--axon_port",
        dest="axon_port",
        type=int,
        required=True,
        help="Miner axon TCP port to manage.",
    )
    parser.add_argument(
        "--blacklist.validator_min_stake",
        dest="validator_min_stake",
        type=int,
        default=65_000,
        help=(
            "Minimum validator stake to admit through the firewall "
            "(should match the miner's --blacklist.validator_min_stake)."
        ),
    )
    parser.add_argument(
        "--chain_sync.interval_seconds",
        dest="interval_seconds",
        type=int,
        default=300,
        help="Seconds between reconciliation cycles.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=(
            "Print the desired validator allowlist from the metagraph and "
            "exit without reading or mutating UFW."
        ),
    )
    parser.add_argument(
        "--network",
        default="finney",
        dest="network",
        type=str,
        help="""The subtensor network flag. The likely choices are:
        -- finney (main network)
        -- test (test network)
        -- archive (archive network +300 blocks)
        -- local (local running network)
    If this option is set it overloads subtensor.chain_endpoint with
    an entry point node from that network.
    """,
    )
    bt.Subtensor.add_args(parser)
    bt.logging.add_args(parser)
    return parser


def main(argv: Iterable[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    config = bt.Config(parser)

    subtensor = bt.Subtensor(config=config, network=args.network)

    if args.dry_run:
        # Preview only: read the metagraph and print the validator IPs that
        # would be allowed, without reading or mutating UFW.
        metagraph = subtensor.metagraph(args.netuid)
        desired = compute_desired_allowlist(
            metagraph, args.validator_min_stake
        )
        print(
            f"Desired allowlist for netuid={args.netuid} "
            f"with min_stake={args.validator_min_stake}: {desired}"
        )
        return 0

    # Fail fast if ufw isn't reachable — running unprivileged or without
    # ufw installed would otherwise silently no-op forever.
    try:
        read_ufw_status(args.axon_port)
    except (RuntimeError, FileNotFoundError) as exc:
        bt.logging.error(
            f"cannot read ufw status (run as root with ufw installed): "
            f"{exc}"
        )
        return 1

    bt.logging.info(
        f"chain-sync started: netuid={args.netuid} "
        f"port={args.axon_port} "
        f"min_stake={args.validator_min_stake} "
        f"interval={args.interval_seconds}s"
    )

    while True:
        try:
            reconcile_once(
                subtensor=subtensor,
                netuid=args.netuid,
                axon_port=args.axon_port,
                min_stake=args.validator_min_stake,
            )
        except Exception as exc:
            # Never mutate UFW on a failed sync — log and try again next tick.
            bt.logging.error(f"reconcile cycle failed: {exc}")
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

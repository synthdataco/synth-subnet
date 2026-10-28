"""Check a miner's generate function with the validator's own response check.

Calls the function once per competition asset at the production path count,
validates each response with synth's validate_responses, flags prices <= 0 and
calls slower than PER_CALL_SECONDS, and exits 1 if any prompt fails. It checks
functions with generate_simulations' signature (the stock miner); a synth-lib
simulate() takes context_prices and has its own checks in synth-lib.

Run from the synth-subnet root with its venv active:

    PYTHONPATH=. python .claude/skills/synth-miner/scripts/check_responses.py
    PYTHONPATH=. python .claude/skills/synth-miner/scripts/check_responses.py \\
        --function my_model:generate --assets BTC,XAU

The function takes (asset, start_time, time_increment, time_length,
num_simulations) and returns the response tuple. The stock one fetches live
prices: set BINANCE_API_HOST where api.binance.com answers HTTP 451.
"""

import argparse
import importlib
import sys
import time

from synth.simulation_input import SimulationInput
from synth.utils.helpers import get_current_time, round_time_to_minutes
from synth.validator.competition_config import ALL_COMPETITIONS
from synth.validator.prompt_config import HIGH_FREQUENCY, LOW_FREQUENCY
from synth.validator.response_validation_v2 import CORRECT, validate_responses

PROMPTS = {p.time_length: p for p in (LOW_FREQUENCY, HIGH_FREQUENCY)}
# The stock miner answers prompts that arrive in the same minute one after
# another, all within the ~50 s of a crypto-1h prompt.
PER_CALL_SECONDS = 5


def load_function(spec: str):
    module_name, _, name = spec.partition(":")
    return getattr(importlib.import_module(module_name), name)


def check(
    generate, asset: str, time_increment: int, time_length: int
) -> tuple[str, float]:
    """(verdict, seconds taken) for one prompt."""
    prompt = PROMPTS[time_length]
    start_time = round_time_to_minutes(
        get_current_time(), prompt.timeout_extra_seconds
    ).isoformat()
    simulation_input = SimulationInput(
        asset=asset,
        start_time=start_time,
        time_increment=time_increment,
        time_length=time_length,
        num_simulations=prompt.num_simulations,
    )
    began = time.perf_counter()
    try:
        response = generate(
            asset,
            start_time=start_time,
            time_increment=time_increment,
            time_length=time_length,
            num_simulations=prompt.num_simulations,
        )
    except Exception as exc:
        return (
            f"raised {type(exc).__name__}: {exc}",
            time.perf_counter() - began,
        )
    elapsed = time.perf_counter() - began
    verdict = validate_responses(response, simulation_input, f"{elapsed:.3f}")
    if verdict != CORRECT:
        return verdict, elapsed
    non_positive = sum(
        1 for path in response[2:] for price in path if price <= 0
    )
    if non_positive:
        return (
            f"{non_positive} price(s) <= 0: a zero price makes the prompt a miss",
            elapsed,
        )
    if elapsed > PER_CALL_SECONDS:
        return (
            f"slower than {PER_CALL_SECONDS} s: prompts that arrive together"
            " are answered one after another",
            elapsed,
        )
    return verdict, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--function",
        default="synth.miner.simulations:generate_simulations",
        help="module:function with generate_simulations' signature",
    )
    parser.add_argument(
        "--assets",
        help="comma-separated subset, default every competition asset",
    )
    args = parser.parse_args()

    known = {a for c in ALL_COMPETITIONS for a in c.asset_list}
    wanted = (
        {a.strip().upper() for a in args.assets.split(",") if a.strip()}
        if args.assets
        else known
    )
    if wanted - known:
        parser.error(f"unknown asset(s): {', '.join(sorted(wanted - known))}")

    generate = load_function(args.function)
    failures = 0
    for competition in ALL_COMPETITIONS:
        for asset in competition.asset_list:
            if asset not in wanted:
                continue
            verdict, elapsed = check(
                generate,
                asset,
                competition.time_increment,
                competition.time_length,
            )
            failures += verdict != CORRECT
            print(
                f"{competition.label:26} {asset:7} {elapsed:6.2f} s  {verdict}"
            )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

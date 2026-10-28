#!/usr/bin/env python3
"""Status of one Synth (subnet 50) miner UID from the public APIs.

Prints the validator's verdict on the UID's latest answer, its rank, reward
weight and smoothed score in each subnet competition, and its Synth Ultra rows
when it has any. With --scores, also each asset's prompts, misses and mean
prompt score since 00:00 UTC yesterday. Standard library only; requests are
paced about a second apart.

    python3 miner_status.py --uid 123
    python3 miner_status.py --uid 123 --scores
"""

import argparse
import gzip
import json
import math
import time
import typing
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.synthdata.co"
MONITORING = "https://monitoring.synthdata.co/v1"
PACE_SECONDS = 1.1
ATTEMPTS = 4

# Slug -> ((time_increment, time_length), assets); must match
# synth/validator/competition_config.py.
COMPETITIONS = {
    "crypto-1h": ((60, 3600), ["BTC", "ETH", "SOL", "XRP", "HYPE"]),
    "crypto-24h": ((300, 86400), ["BTC", "ETH", "SOL", "XRP", "HYPE"]),
    "com-equ-24h": (
        (300, 86400),
        [
            "XAU",
            "SP500",
            "NVDAX",
            "GOOGLX",
            "TSLAX",
            "AAPLX",
            "WTIOIL",
            "SPCX",
        ],
    ),
}
# Prefix of the Synth Ultra labels in /rewards/scores rows.
ULTRA_LABEL = "VHFT 10s"

_last_request = 0.0


def get(url: str, params: dict) -> typing.Any:
    """JSON from a GET, or None when the API has no data: 404, or 500 from
    /validation/miner for a UID without predictions.

    Retries on 429 and other 5xx.
    """
    global _last_request
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={
            "Accept-Encoding": "gzip",
            "User-Agent": "synth-miner-status/1.0",
        },
    )
    for attempt in range(ATTEMPTS):
        time.sleep(max(0.0, PACE_SECONDS - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or (
                exc.code == 500 and url.endswith("/validation/miner")
            ):
                return None
            if exc.code != 429 and exc.code < 500:
                detail = exc.read().decode(errors="replace")[:200]
                raise SystemExit(
                    f"{request.full_url}: HTTP {exc.code} {detail}"
                )
            time.sleep(2 ** (attempt + 1))
    raise SystemExit(
        f"{request.full_url}: still failing after {ATTEMPTS} attempts"
    )


def show_validation(uid: int) -> None:
    verdict = get(f"{API}/validation/miner", {"uid": uid})
    if verdict is None:
        print("latest answer: none recorded for this UID")
        return
    status = (
        "valid"
        if verdict.get("validated")
        else f"INVALID: {verdict.get('reason')}"
    )
    response_time = verdict.get("response_time")
    took = (
        f" (response time {float(response_time):.1f} s)"
        if response_time
        else ""
    )
    print(f"latest answer: {status}{took}")


def show_standings(uid: int) -> None:
    print(
        "\ncompetition   rank       reward_weight  share   smoothed_score"
        "  updated"
    )
    for slug in COMPETITIONS:
        board = (
            get(
                f"{MONITORING}/competitions/{slug}/leaderboard",
                {"asset": "Overall", "page": 1, "page_size": 500},
            )
            or {}
        )
        rows = board.get("rows") or []
        row = next((r for r in rows if r["miner_uid"] == uid), None)
        if row is None:
            print(
                f"{slug:13} not on the leaderboard"
                f" (source {board.get('source', '-')})"
            )
            continue
        total = sum(r["reward_weight"] for r in rows)
        share = 100 * row["reward_weight"] / total if total else 0.0
        rank = f"{row['rank']}/{board.get('total', len(rows))}"
        print(
            f"{slug:13} {rank:10} {row['reward_weight']:13.6f}"
            f"  {share:5.2f}%  {row['smoothed_score']:14.3f}"
            f"  {board.get('updated_at', '-')}"
        )
    print("lower smoothed_score is better; share is within the competition")


def show_ultra(uid: int, today: str) -> None:
    rows = (
        get(
            f"{API}/rewards/scores",
            {"from": today, "to": today, "miner_uid": uid},
        )
        or []
    )
    latest: dict[str, dict] = {}
    for row in rows:
        label = row["prompt_name"]
        newest = latest.get(label, {}).get("updated_at", "")
        if label.startswith(ULTRA_LABEL) and row["updated_at"] > newest:
            latest[label] = row
    if not latest:
        print("\nSynth Ultra: no rows today")
        return
    print("\nSynth Ultra (lower smoothed_score is better)")
    for label, row in sorted(latest.items()):
        print(
            f"{label:16} reward_weight {row['reward_weight']:.6f}"
            f"  smoothed_score {row['smoothed_score']:.6g}"
            f"  {row['updated_at']}"
        )


def show_scores(uid: int, start: str, end: str) -> None:
    print(
        f"\nprompts scored since {start} 00:00 UTC: crps -1 is a miss;"
        " prompt_score is 0 for the best answer and includes misses"
    )
    for slug, ((increment, length), assets) in COMPETITIONS.items():
        for asset in assets:
            rows = (
                get(
                    f"{API}/validation/scores/historical",
                    {
                        "from": start,
                        "to": end,
                        "miner_uid": uid,
                        "asset": asset,
                        "time_increment": increment,
                        "time_length": length,
                    },
                )
                or []
            )
            misses = sum(1 for row in rows if row["crps"] == -1)
            mean = (
                sum(row["prompt_score"] for row in rows) / len(rows)
                if rows
                else math.nan
            )
            print(
                f"{slug:13} {asset:7} prompts {len(rows):4}  misses {misses:4}"
                f"  mean prompt_score {mean:9.3f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument(
        "--scores",
        action="store_true",
        help="also per-asset prompts and misses since 00:00 UTC yesterday"
        " (18 more requests)",
    )
    args = parser.parse_args()
    if not 0 <= args.uid < 256:
        parser.error("a subnet 50 UID is between 0 and 255")

    today = datetime.now(timezone.utc).date()
    print(f"Synth miner UID {args.uid} (mainnet, netuid 50)")
    show_validation(args.uid)
    show_standings(args.uid)
    show_ultra(args.uid, today.isoformat())
    if args.scores:
        yesterday = (today - timedelta(days=1)).isoformat()
        show_scores(args.uid, yesterday, today.isoformat())


if __name__ == "__main__":
    main()

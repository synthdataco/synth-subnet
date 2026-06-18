"""
One-shot discovery script for the Pyth Pro migration.

Goals:
1. List the Pyth Lazer feed catalog and map our existing 32-byte hermes IDs
   to the new u32 `pyth_lazer_id` values.
2. Determine which symbol string format the Pyth Pro Router history endpoint
   accepts ("BTC/USD" vs "Crypto.BTC/USD"), so we know what to put in the
   validator's PYTH_SYMBOL_MAP after the migration.

Run from the synth-subnet repo root:
    python verify/pyth-lazer-listing.py

Requires PYTH_API_KEY in .env (free key from pythdata.app -> Pyth Terminal).
"""

import os
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv

# Pyth Pro Router (TradingView-shaped, PUBLIC — no auth on /v1/symbols or
# /v1/{channel}/history; same `symbol` strings as the legacy Benchmarks
# API). The channel in the path matters: `real_time` returns 404 for feeds
# whose `min_channel > real_time` (stocks/metals/oil); `fixed_rate@200ms`
# works for every feed and is what production code uses.
ROUTER_SYMBOLS_URL = "https://pyth.dourolabs.app/v1/symbols"
ROUTER_HISTORY_URL = "https://pyth.dourolabs.app/v1/fixed_rate@200ms/history"

# Pyth Lazer (low-latency POST endpoints, requires Bearer access_token).
LAZER_LATEST_PRICE_URL = "https://pyth-lazer.dourolabs.app/v1/latest_price"

# 32-byte hermes hex IDs that synth-subnet currently uses (synth/miner/price_simulation.py).
HERMES_ID_MAP = {
    "BTC": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "XAU": "44465e17d2e9d390e70c999d5a11fda4f092847fcd2e3e5aa089d96c98a30e67",
    "SOL": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "SPYX": "2817b78438c769357182c04346fddaad1178c82f4048828fe0997c3c64624e14",
    "NVDAX": "4244d07890e4610f46bbde67de8f43a4bf8b569eebe904f136b469f148503b7f",
    "TSLAX": "47a156470288850a440df3a6ce85a55917b813a19bb5b31128a33a986566a362",
    "AAPLX": "978e6cc68a119ce066aa830017318563a9ed04ec3a0a6439010fc11296a58675",
    "GOOGLX": "b911b0329028cd0283e4259c33809d62942bd2716a58084e5f31d64c00b5424e",
    "XRP": "ec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "HYPE": "4279e31cc369bbcc2faf022b382b080e32a8e689ff20fbc530d2a603eb6cd98b",
    # WTIOIL's legacy hermes_id is missing from the new Lazer catalog (the
    # crypto-style WTI feed was deprecated). The closest spot replacement is
    # `Commodities.USOILSPOT` (pyth_lazer_id=657, "WTI LIGHT SWEET CRUDE OIL
    # CFD"). We map WTIOIL to that feed's hermes_id so the discovery probe
    # picks it up.
    "WTIOIL": "925ca92ff005ae943c158e3563f59698ce7e75c5a8c8dd43303a0a154887b3e6",
}

# Assets with no hermes_id in the Lazer catalog — Pyth-Pro / Lazer-only feeds.
# SPCX is `Pyth.HL.SPCX/USDC` (pyth_lazer_id=99934, exponent=-4): the legacy
# hermes/Benchmarks endpoint returns "Symbol ... doesn't exist" for it, so the
# validator can only score SPCX with PYTH_BACKEND=pro. The hermes_id bridge
# can't resolve these, so we match them by symbol string instead.
LAZER_SYMBOL_MAP = {
    "SPCX": "Pyth.HL.SPCX/USDC",
}


def lazer_auth_headers() -> dict:
    key = os.environ.get("PYTH_API_KEY")
    if not key:
        print(
            "ERROR: PYTH_API_KEY not set in environment / .env",
            file=sys.stderr,
        )
        sys.exit(1)
    return {"Authorization": f"Bearer {key}"}


def normalize_hex(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.lower().strip()
    if v.startswith("0x"):
        v = v[2:]
    return v


def fetch_router_symbols() -> list:
    """Router /v1/symbols is public; carries the hermes_id <-> pyth_lazer_id
    bridge field that we need to build the migration mapping."""
    resp = requests.get(ROUTER_SYMBOLS_URL, timeout=30)
    if resp.status_code != 200:
        print(
            f"ERROR: GET {ROUTER_SYMBOLS_URL} -> {resp.status_code}",
            file=sys.stderr,
        )
        print(resp.text[:500], file=sys.stderr)
        sys.exit(2)
    return resp.json()


def map_assets_to_lazer(symbols: list) -> dict:
    by_hermes = {}
    for entry in symbols:
        hermes_id = normalize_hex(entry.get("hermes_id"))
        if hermes_id:
            by_hermes[hermes_id] = entry

    by_symbol = {entry.get("symbol"): entry for entry in symbols}

    mapping = {}
    for asset, hermes_id in HERMES_ID_MAP.items():
        match = by_hermes.get(normalize_hex(hermes_id))
        mapping[asset] = match
    # Lazer-only feeds have no hermes_id; resolve them by symbol instead.
    for asset, symbol in LAZER_SYMBOL_MAP.items():
        mapping[asset] = by_symbol.get(symbol)
    return mapping


def probe_history_symbol(symbol: str) -> tuple:
    """Probe the Pro Router history endpoint with a given symbol string.

    Public endpoint, no auth. Returns (status_code, s, num_candles, errmsg).
    """
    now = int(time.time())
    params = {
        "symbol": symbol,
        "resolution": 1,
        "from": now - 600,
        "to": now,
    }
    resp = requests.get(ROUTER_HISTORY_URL, params=params, timeout=30)
    body = {}
    try:
        body = resp.json()
    except Exception:
        pass
    s = body.get("s")
    t_arr = body.get("t") or []
    errmsg = body.get("errmsg")
    return resp.status_code, s, len(t_arr), errmsg


def probe_lazer_latest_price(
    pyth_lazer_id: int, channel: str = "fixed_rate@200ms"
) -> tuple:
    """Probe Lazer POST /v1/latest_price with one feed.

    Returns (status_code, parsed_price_str_or_None, exponent_or_None, error).
    """
    payload = {
        "channel": channel,
        "priceFeedIds": [pyth_lazer_id],
        "properties": ["price", "exponent", "publisherCount"],
        "formats": [],
        "parsed": True,
        "jsonBinaryEncoding": "hex",
    }
    resp = requests.post(
        LAZER_LATEST_PRICE_URL,
        json=payload,
        headers=lazer_auth_headers(),
        timeout=30,
    )
    if resp.status_code != 200:
        return resp.status_code, None, None, resp.text[:200]
    body = resp.json()
    parsed = body.get("parsed") or {}
    feeds = parsed.get("priceFeeds") or []
    if not feeds:
        return resp.status_code, None, None, "no priceFeeds in parsed"
    feed = feeds[0]
    return resp.status_code, feed.get("price"), feed.get("exponent"), None


def main() -> None:
    load_dotenv()

    print("=" * 70)
    print("Pyth Pro Router symbols catalog")
    print("=" * 70)
    symbols = fetch_router_symbols()
    print(f"Fetched {len(symbols)} symbol entries\n")

    print("=" * 70)
    print("Mapping our 11 assets by hermes_id")
    print("=" * 70)
    mapping = map_assets_to_lazer(symbols)
    print(f"{'ASSET':<8}{'LAZER_ID':<12}{'SYMBOL':<24}{'EXPONENT':<10}NAME")
    print("-" * 70)
    for asset, entry in mapping.items():
        if entry is None:
            print(f"{asset:<8}MISSING")
            continue
        print(
            f"{asset:<8}"
            f"{str(entry.get('pyth_lazer_id')):<12}"
            f"{str(entry.get('symbol')):<24}"
            f"{str(entry.get('exponent')):<10}"
            f"{entry.get('name')}"
        )
    print()

    print("=" * 70)
    print("Pro Router /history symbol-format probe (BTC)")
    print("=" * 70)
    for candidate in ("BTC/USD", "Crypto.BTC/USD"):
        code, status, n, err = probe_history_symbol(candidate)
        print(
            f"symbol={candidate!r:<22} -> http={code} s={status!r} "
            f"candles={n} errmsg={err!r}"
        )

    print()
    print("=" * 70)
    print("Lazer POST /v1/latest_price sanity check (BTC)")
    print("=" * 70)
    print(
        "Channel: fixed_rate@200ms (universal — meets every feed min_channel)"
    )
    for asset, entry in mapping.items():
        if entry is None:
            print(f"{asset:<8} (unmapped — fallback needed)")
            continue
        lazer_id = entry.get("pyth_lazer_id")
        code, price_mantissa, exponent, err = probe_lazer_latest_price(
            int(lazer_id)
        )
        if err:
            print(
                f"{asset:<8} lazer_id={lazer_id:<6} http={code} error={err!r}"
            )
            continue
        try:
            price = float(price_mantissa) * (10 ** int(exponent))
            print(
                f"{asset:<8} lazer_id={lazer_id:<6} http={code} "
                f"mantissa={price_mantissa} exp={exponent} -> {price}"
            )
        except (TypeError, ValueError):
            print(
                f"{asset:<8} lazer_id={lazer_id} http={code} "
                f"raw price={price_mantissa!r} exponent={exponent!r}"
            )


if __name__ == "__main__":
    main()

# The miner protocol

Contents:
- The request
- The response contract
- Validation messages
- Where to plug a model in
- Anchoring on the scored price
- Who may query the miner
- Concurrency and latency
- Checking output before deploying

Source files on synth-subnet `main`:
- `synth/protocol.py` and `synth/simulation_input.py`: the request and response types.
- `neurons/miner.py` and `synth/base/miner.py`: the reference miner.
- `synth/validator/response_validation_v2.py`: the format check.
- `synth/validator/prompt_config.py`: prompt sizes and deadlines.

## The request

Validators POST the bittensor synapse `Simulation` to `http://<published ip>:<port>/Simulation`.
Its `simulation_input` is a `SimulationInput`:

| Field             | Type           | Value in production                                                          |
| ----------------- | -------------- | ---------------------------------------------------------------------------- |
| `asset`           | `str`          | One of the competition assets, e.g. `BTC`, `SP500`                           |
| `start_time`      | ISO-8601 `str` | A whole UTC minute with an explicit offset, e.g. `2026-10-26T12:03:00+00:00` |
| `time_increment`  | `int`, seconds | 300 (24h competitions) or 60 (`crypto-1h`)                                   |
| `time_length`     | `int`, seconds | 86400 or 3600                                                                |
| `num_simulations` | `int`          | 1000                                                                         |

- **No `sigma`.** There is no `sigma` field. Docs that say "use every parameter except sigma" date
  from an older prompt format.
- **How `start_time` is set.** It is the next whole minute after the request, plus 60 s for 24h
  prompts and plus 0 s for 1h prompts.
- **The deadline is `start_time`.** The validator enforces `start_time − now` as one wall-clock
  limit for the whole HTTP call. That leaves about 110–120 s for a 24h prompt and about 50–60 s for
  a 1h prompt (the CHANGELOG says to expect about 50 s). A late answer is discarded, even though the
  miner keeps computing it.
- **Inside `forward_miner`:** `synapse.dendrite.hotkey` is the calling validator, and
  `synapse.timeout` is the budget in seconds at dispatch. `start_time` stays the true deadline even
  if the request waited in a queue.
- **Requests come in bursts at minute boundaries.**
  - Every validator runs its 24h and 1h prompt loops in separate processes, and queries every UID.
  - One validator can send several 1h prompts and a 24h prompt in the same minute.
  - Other validators add their own. The rate is a validator setting, and the CHANGELOG announces
    changes.
  - To see the current pattern, list prompt start times with `/validation/prompts`
    ([api-and-monitoring.md](api-and-monitoring.md)). Arrival is `start_time` minus 60 s (1h) or
    minus 120 s (24h).
- **Only a sample is scored.** One prompt per asset per hour (24h) or per 10 minutes (1h) ends up
  scored, and you cannot tell which in advance.

## The response contract

Set `synapse.simulation_output` to a tuple or list:

```
(start_timestamp, time_increment, path_1, path_2, ..., path_N)
```

| Rule                                                                                          | Why it matters                                                                         |
| --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `start_timestamp` is an `int` equal to `int(datetime.fromisoformat(start_time).timestamp())`  | Fractional floats, local-time parses and "now" all fail. Send a real `int`             |
| `time_increment` is an `int` equal to the prompt's value                                      | Echo the prompt; never hard-code 300                                                   |
| Exactly `num_simulations` paths                                                               | 999 or 100 paths and the whole response is rejected                                    |
| Each path is a list of `time_length // time_increment + 1` numbers: 289 or 61                 | The first point is the price at `start_time`; including t0 is mandatory                |
| Each price is finite                                                                          | A NaN or inf cannot be serialised: the axon answers HTTP 500, and the prompt is a miss |
| Each price is inside float32 range (≤ ~3.4e38, and not a non-zero value that underflows to 0) | The validator stores predictions as float32                                            |
| Each price has at most 8 significant digits: `float(f"{p:.7e}") == p`                         | Raw float64 output fails with "too many digits"                                        |
| Every price is strictly positive                                                              | A single 0 passes the format check but turns the whole prompt into a miss at scoring   |

- **All or nothing.** One bad point anywhere invalidates the whole response. There is no partial
  credit.
- **Path order is irrelevant,** because ensemble CRPS does not care about order.
- **The time structure inside each path matters.** Scoring uses changes within a path over
  5-minute, 30-minute and other intervals, and in `crypto-1h` the volatility of each path.
  - Never shuffle or sort values across paths at a time step.
  - Never build paths by stitching together independent marginals.
- **Rounding.** Round as the last step, in float64:
  - `synth.utils.helpers.convert_prices_to_time_format(prices, start_time_str, time_increment)` does
    the whole conversion;
  - `round_to_8_significant_digits(p)` rounds one price;
  - so does `float(f"{p:.7e}")`.

  A float32 array converted to Python floats fails: `float(np.float32(104856.23))` is
  `104856.2265625`, which has too many digits.
- **What gets coerced on the way out.** The response field is a pydantic model field, so some values
  are converted before sending:
  - numpy arrays and tuples become lists;
  - a whole-number float in the first two slots becomes an `int`;
  - `True` and `False` become 1 and 0. A `False` therefore passes the format check, then scores as
    a miss;
  - a bare 2-D array, without the two leading integers, fails with "Start time format is
    incorrect".
- **Size.** 1000 × 289 prices is about 3 MB of JSON. The base miner gzips any response of 1 KB or
  more (since v1.13.0), and validators accept gzip transparently.
  - The `response_time` validators measure includes the upload of that payload.
  - A miner built on an older template, or on its own axon, should add the same compression, and
    needs enough upload bandwidth.

## Validation messages

The validator records one verdict per prompt. `https://api.synthdata.co/validation/miner?uid=<UID>`
shows the latest one: `validated: true` with an empty `reason` when it was valid, otherwise the
message below as `reason`. The first failing check wins, in this order:

| `reason`                                                                          | Meaning and fix                                                                                                                                                                         |
| --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| *(empty, with `validated: true`)*                                                 | Valid                                                                                                                                                                                   |
| `time out or internal server error (process time is None)`                        | No HTTP 200 arrived before `start_time`: unreachable, blacklisted, too slow, an exception, or a NaN/inf price (the axon answers HTTP 500). See [troubleshooting.md](troubleshooting.md) |
| `Response is empty`                                                               | `simulation_output` was `None` or empty                                                                                                                                                 |
| `Response format is incorrect: expected tuple or list, got …`                     | A dict, a string or a number was returned                                                                                                                                               |
| `Start time format is incorrect: expected int, got …`                             | Element 0 is a fractional float or a list (often a bare array)                                                                                                                          |
| `Time increment format is incorrect: expected int, got …`                         | Element 1 is not an `int`                                                                                                                                                               |
| `Start time timestamp is incorrect: expected X, got Y`                            | Derived from the clock or a naive datetime. Parse the prompt's `start_time`                                                                                                             |
| `Time increment is incorrect: expected X, got Y`                                  | Hard-coded increment. A 24h shape was sent to a 1h prompt                                                                                                                               |
| `Number of paths is incorrect: expected N, got M`                                 | Hard-coded path count                                                                                                                                                                   |
| `Path format is incorrect: expected list, got …`                                  | A number or string sits where a path should be                                                                                                                                          |
| `Number of time points is incorrect: expected 289, got 288`                       | The t0 point is missing, or the wrong shape was sent (`expected 61, got 289`)                                                                                                           |
| `Price format is incorrect: too many digits <p>`                                  | Missing 8-significant-digit rounding                                                                                                                                                    |
| `Price format is incorrect: exceeds float32 range <p>` / `underflows float32 <p>` | The paths exploded or collapsed                                                                                                                                                         |
| `Price format is incorrect: expected int or float, got …`                         | `None` or a non-numeric string inside a path                                                                                                                                            |
| `Price format is incorrect: non-finite value <p>`                                 | Not seen from an axon miner, where a NaN or inf becomes HTTP 500 instead                                                                                                                |
| `error during validation`                                                         | The validator crashed while checking. Usually a response with fewer than two elements                                                                                                   |

The `response_time` field is the time the validator measured, in seconds. It counts for nothing as
long as it lands before the deadline: latency is not part of the score.

## Where to plug a model in

**Option 1: replace `generate_simulations`.** This is the path the docs describe. Edit
`synth/miner/simulations.py` and keep this signature and return format:

```python
def generate_simulations(asset="BTC", start_time: str = "", time_increment=300, time_length=86400, num_simulations=1):
    ...
    return convert_prices_to_time_format(paths.tolist(), start_time, time_increment)
```

- **Upstream changes this file often.** `SIGMA_MAP` and the price maps change with every asset
  change, so in-place edits conflict on `git pull`. For a long-lived miner, prefer Option 2.
- **The stock body is a placeholder:**
  - it fetches the spot price (`get_asset_price`);
  - it draws zero-drift Gaussian simple returns with a fixed per-asset volatility from `SIGMA_MAP`,
    quoted per √hour; SPCX is missing and falls back to 0.005;
  - it compounds them.

  It has no fat tails, no volatility clustering and no intraday pattern. Expect it to rank low.

**Option 2: your own module and a `Miner` subclass.** This keeps tracked files untouched, and lets
you offload work, cache state, or call a model server:

```python
import asyncio
import time

import bittensor as bt

from neurons.miner import Miner
from synth.protocol import Simulation

from my_model import generate  # same signature and return format as generate_simulations


class MyMiner(Miner):
    async def forward_miner(self, synapse: Simulation) -> Simulation:
        si = synapse.simulation_input
        bt.logging.info(f"request {si.asset} {si.time_length} start {si.start_time}")
        synapse.simulation_output = await asyncio.to_thread(
            generate, si.asset, si.start_time, si.time_increment, si.time_length, si.num_simulations
        )
        return synapse


if __name__ == "__main__":
    with MyMiner() as miner:
        while True:
            miner.print_info()
            time.sleep(20)
```

Run it like `neurons/miner.py`, from the synth-subnet root with `PYTHONPATH=.` and the same flags.
Constraints:
- **Annotations must be real classes.** bittensor's `axon.attach` reads the class from the
  annotation of `forward_miner`'s first parameter. A module with
  `from __future__ import annotations` fails at startup with
  `TypeError: issubclass() arg 1 must be a class`.
- **Keep the exact signatures** if you override `blacklist(self, synapse: Simulation) ->
  typing.Tuple[bool, str]` or `priority(self, synapse: Simulation) -> float`, including the
  parameter name `synapse`.
- **Threads.** `asyncio.to_thread` lets overlapping requests run at the same time.
  - It helps I/O, and numpy work that releases the GIL.
  - Pure-Python compute holds the GIL: use a process pool (`loop.run_in_executor` with a
    `ProcessPoolExecutor`) or a separate model server.
  - A model with module state must not run on two threads at once; guard it with a lock.
- **Log the request yourself,** as above. The stock `Received prediction request from` line lives
  in the method you replaced.
- **Have a fallback when the model can fail.** An exception becomes HTTP 500, and an empty answer
  is rejected as `Response is empty`: both are misses, so a bare `try/except` changes nothing.
  - Answer instead from something cheap and sane, such as the last good fan re-anchored on a fresh
    price, or a constant-volatility fan, and log the failure.
  - Never fall back to a hardcoded or stale price: an absurd answer scores worse than a miss.

**Option 3: synth-lib's serving shell** serves a `simulate(..., context_prices)` function and
provides the market data. See [deployment.md](deployment.md) §D.

## Anchoring on the scored price

The validator builds the realized path on the grid `t_i = start_time + i × time_increment`. Each
`t_i` takes the **close of the 1-minute candle whose open time is `t_i`**, i.e. the last trade in
`[t_i, t_i + 60 s)`. Venues:

| Assets                                                | Venue and instrument                                                                                                    |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| BTC, ETH, SOL, XRP                                    | Binance spot `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT` (`/api/v3/klines` at `BINANCE_API_HOST`)                        |
| HYPE                                                  | Hyperliquid spot `@107` (HYPE/USDC), `candleSnapshot` 1m at `https://api.hyperliquid.xyz/info`                          |
| XAU, SP500, NVDAX, GOOGLX, TSLAX, AAPLX, WTIOIL, SPCX | Hyperliquid `xyz` perps: `xyz:GOLD`, `xyz:SP500`, `xyz:NVDA`, `xyz:GOOGL`, `xyz:TSLA`, `xyz:AAPL`, `xyz:CL`, `xyz:SPCX` |

- **Take `path[0]` from the same venue and instrument,** as close to `start_time` as you can.
  - The `xyz` perps trade at their own level with their own basis: `xyz:SP500` is not the cash
    index.
  - The anchor's level only enters the absolute final-price term (`24hour_abs`, `60min_abs`).
  - The change terms, `crypto-1h` gaps included, are measured from each path's own `path[0]`, so
    the anchor level does not move them.
- **The stock price fetch is slow on failure.** It uses a 30 s timeout and up to 5 attempts, with
  backoff sleeps, on exceptions; a non-200 answer such as HTTP 451 returns at once and the prompt
  fails. All of it runs on the request path and can blow a 1h deadline on its own. Refresh prices
  in a background thread and read the cached value instead.
- **Compare with the truth.** `GET /validation/realized-path` returns the exact path a prompt was
  scored against (see [api-and-monitoring.md](api-and-monitoring.md)).

## Who may query the miner

`Miner.blacklist` rejects a request (HTTP 403, logged with a reason) unless every check passes, in
this order:

1. A dendrite and a hotkey are present.
2. The request is signed. bittensor checks a signature only when one is present, so this check is
   what blocks unsigned spoofed requests.
3. The hotkey is registered in the miner's metagraph.
4. That validator publishes an IP on chain (`is_serving`).
5. The stake is strictly greater than `max(65000, --blacklist.validator_min_stake)`. The floor
   cannot be lowered with a flag.
6. The hotkey holds a validator permit.

After step 4 the blacklist logs `Requesting UID: <uid> | Stake at UID: <stake>`, on every path:
that line is the universal "a validator reached me" signal. The validator records a rejected request
as a miss, so raising `--blacklist.validator_min_stake` above a real validator's stake costs you
every prompt that validator sends. Keep the default unless you know why.

## Concurrency and latency

- **Requests run one at a time.** `forward_miner` is `async`, but the stock body calls synchronous
  code, which blocks the axon's single event loop.
  - Raising `--axon.max_workers` or rewriting `priority()` does not change that.
  - In bittensor 10.4.1 neither parallelises nor reorders forwards.
- **Budget for the worst minute.** The last request in a burst finishes after everything queued
  before it, and must still beat the 1h deadline (about 50 s).
  - For example, eight queued requests at 5 s each just fit; at 10 s each, the fifth onward misses.
  - Keep each call to about 5 s at 1000 paths.
  - If you queue work yourself, take the earliest `start_time` first: a 1h prompt is due a minute
    before a 24h prompt that arrived with it.
- **Move fitting off the request path.** Fit volatility and regime state in a background loop, and
  only sample paths at request time. The synth-lib shell does exactly this with market data.
- **Measure.** Compare `start_time` with the time each request is logged.
  - bittensor stamps logs in the host's local time, and `start_time` is UTC; run the host with
    `TZ=UTC`, or convert.
  - `response_time` on `/validation/miner` shows one validator's view.
- **Clock.** Keep the clock NTP-synced. A request that waited behind a blocked event loop, or a
  skewed clock, gets HTTP 401 `Nonce is too old`.

## Checking output before deploying

From the synth-subnet root, with the venv active:

```bash
PYTHONPATH=. python .claude/skills/synth-miner/scripts/check_responses.py                       # stock generate_simulations
PYTHONPATH=. python .claude/skills/synth-miner/scripts/check_responses.py --function my_model:generate
PYTHONPATH=. python .claude/skills/synth-miner/scripts/check_responses.py --assets BTC,XAU       # a subset
```

The script:
- builds a prompt for every competition shape: each asset, at 1000 paths, with a realistic
  `start_time`;
- calls the function and runs the validator's own `validate_responses` on the raw result. That is
  stricter than live: it also flags numpy rows, which the miner would convert;
- flags zero or negative prices, and any call slower than 5 s.

Notes:
- A stock generate function calls the price venues, so set `BINANCE_API_HOST` first on a
  geo-blocked host.
- The script exits with status 1 if any prompt fails.
- It is for functions with `generate_simulations`' signature. A synth-lib `simulate()` has its own
  checks ([deployment.md](deployment.md) §D).

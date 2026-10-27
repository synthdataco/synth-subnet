# Improving the model: data, backtests and iteration

Contents:
- Frame the problem
- Market data
- Generating predictions without lookahead
- The backtester
- Diagnosing `vol_crps`
- Rules that keep results honest
- The iteration loop
- Studying the field
- Shipping a model

The tools here are all in **synth-lib** (https://github.com/synthdataco/synth-lib, Python ≥ 3.12):

```bash
git clone https://github.com/synthdataco/synth-lib.git && cd synth-lib && uv sync
```

Run every synth-lib command from the synth-lib root. `market_data/`, `miner_outputs/` and
`offline_data/` resolve against the working directory.

synth-lib's brief for autonomous research campaigns,
`synth_lib/benchmark/constitution/CAMPAIGN.md.tmpl`, is worth reading before a long campaign.
- Its method applies to any miner research: a hypothesis and a kill threshold written before each
  experiment, two windows of opposite regimes, one change at a time, a journal, no lookahead and
  seeded randomness.
- Its campaign rules apply only inside a synth-lib benchmark run: a frozen price snapshot with no
  downloads, a credit budget, no use of the operator's existing models, and a mandatory champion.

## Frame the problem

- **The objective is CRPS,** per competition and per asset, at 1000 paths.
  - Rank and reward share are outcomes. They are noisy because the field is dense.
  - Never trade a CRPS regression for a better-looking rank.
- **What the score asks for** (see [scoring.md](scoring.md)):
  - the right distribution of returns over every scored interval: 5 min, 30 min, 3 h and the full
    24 h; for 1h, 1–60 min;
  - a correct final-price spread;
  - for `crypto-1h`, the right minute-to-minute volatility inside every 5-, 15- and 60-minute block.
- **Where the error usually is:**
  - **Volatility level** per asset and horizon.
  - **Time-of-day and day-of-week seasonality.** The tokenised equity and commodity perps trade
    24/7, but their volatility follows the underlying market's hours: high at the US cash open,
    quiet overnight and at weekends. A constant per-asset volatility is too wide at night and too
    narrow at the open. Measure it per asset.
  - **Fat tails and volatility clustering.**
  - **Scheduled events:** CPI, payrolls, FOMC, earnings, exchange holidays.
  - **Anchoring.** Use the right venue and the last real print.
- **Price a residual before fitting it.** For a Gaussian fan, using scale σ instead of the right σ*
  costs about `¼ (ln σ/σ*)²` of CRPS. A ±20% global scale error costs only about 1%. Big systematic
  errors matter; micro-tuning rarely does.
- **Test a feature by what it adds** on top of a fast EWMA of realised volatility, not by its own
  predictive power. Range estimators, volume and trade counts mostly act through realised
  volatility already. What an EWMA cannot hold is information from outside the window: calendar
  events, and anything older than 7 days.
- **Know what the serving shell provides.** In synth-lib serving, `simulate()` sees one asset's
  7-day minute context. Features that need another asset's live price need your own data plumbing.

## Market data

synth-lib downloads minute bars from the venue the validator scores each asset against:
- **Binance spot:** BTC, ETH, SOL, XRP.
- **Hyperliquid:** HYPE and every com-equ asset.

```bash
uv run synth_lib/preparation/market_data.py                                        # all assets, 15 months to yesterday
uv run synth_lib/preparation/market_data.py --asset BTC --days 3                   # the last N days, plus today
uv run synth_lib/preparation/market_data.py --asset BTC --start 2026-09-01 --end 2026-10-20   # inclusive window; resumable
uv run synth_lib/preparation/market_data.py --asset BTC --force-refresh            # rewrite existing partitions
```

- **Layout.** Data lands in `market_data/prices/<ASSET>/1m/date=YYYY-MM-DD.parquet`, with columns
  `timestamp, open, high, low, close, volume, trade_count, …`. Bars are labelled by their **open**
  time in UTC. `close` is the scored series.
- **NaN means no trade in that minute,** not "market closed": the venues trade 24/7. Thin assets
  have many NaN minutes. Never interpolate them, because invented prices fool your own validation.
- **Loaders.** The store's strict loaders (`MinutePriceStore.load_range`, `get_context_window`)
  raise on any NaN close. Use the lenient
  `synth_lib.benchmark.generate_predictions.load_minute_prices` for thin assets.
- **Hyperliquid keeps about 5000 one-minute candles (~3.5 days).** Older HYPE and com-equ days
  come back empty from the venue.
  - For scoring, the backtester falls back to the validator's own realised paths
    (`/validation/realized-path`) and caches them under `market_data/realized/`.
  - Those old days cannot serve as model context from the venue.
  - If you need longer Hyperliquid history, record it yourself from day one. Keep only closed
    candles: the newest candle in a live response is still forming.
- **Geo-blocking.** Set `BINANCE_API_HOST=https://data-api.binance.vision` where `api.binance.com`
  answers HTTP 451.
- **Optional event calendar.**
  `FRED_API_KEY=… uv run python -m synth_lib.preparation.event_calendar.build --start 2025-08-01`
  writes `market_data/calendar/events.parquet`: CPI, NFP, PPI, PCE, retail sales, GDP, FOMC, NYSE
  closures, EIA petroleum and earnings. A model reads its path from `SYNTH_BENCHMARK_CALENDAR`.
  The serving shell sets that variable; for backtests, export it yourself before generating:
  `export SYNTH_BENCHMARK_CALENDAR=$PWD/market_data/calendar/events.parquet`.
- **Untraded minutes.** Treat a minute as a real print only when `close` and `volume` are both
  present, and compute returns only between adjacent real prints. That gives the same volatility
  in backtests (NaN closes) and in serving (filled closes); see [deployment.md](deployment.md) §D.

## Generating predictions without lookahead

Write the model as a `simulate()` function (the contract is in [deployment.md](deployment.md) §D),
then generate predictions with synth-lib's generator:

```bash
uv run python -m synth_lib.benchmark.generate_predictions \
  --modeling champions/my_model/modeling.py --asset BTC \
  --window-start 2026-10-01 --window-end 2026-10-15 \
  --data-root market_data --out-dir miner_outputs/my_model/predictions \
  --time-increment 300 --time-length 86400 --cadence-minutes 60
# crypto-1h: --time-increment 60 --time-length 3600 --cadence-minutes 10
# to answer the validator's real prompt times, add --prompt-times prompt_times.json (a JSON list of
# ISO start times); keep --cadence-minutes, which is still required but then ignored
```

- **No lookahead.** The generator ends each context at `context_end`: `start − 120 s` for 1h
  prompts and `start − 180 s` for 24h prompts. The validator sends the prompt about 60 or 120 s
  before `start_time`, and the bar labelled `t` closes at `t + 60 s` and is the first scored price.
  - A context sliced as `prices.loc[:start_time]` leaks that price.
  - So does `MinutePriceStore.get_context_window(start_time)`, which includes the bar at `t`.
- **Use the scored start times,** `scored_time − time_length`, rather than an even grid.
  - A cadence that divides the hour puts every prompt on the round hour, where volatility spikes.
    The scored prompt can sit at any minute of its bucket.
  - `/validation/prompts` lists every prompt sent, about 10× as many as are scored for
    `crypto-1h` (1435 against 144 for BTC on 2026-10-26), and the backtester scores only the
    scored ones.
  - Take them from the offline bundle you backtest against. The window below is the bundle's:
    from `--eval-end` minus `--days` to `--eval-end`.

    ```bash
    SYNTH_BACKTESTER_OFFLINE_DATA_ROOT=offline_data/crypto-1h uv run python - <<'EOF'
    import json
    from datetime import datetime, timezone

    import pandas as pd

    from synth_lib.backtester.loading import get_miner_scores

    start = datetime(2026, 10, 10, tzinfo=timezone.utc)
    end = datetime(2026, 10, 20, tzinfo=timezone.utc)
    scores = get_miner_scores(start, end, "BTC", 3600, 60)
    times = sorted(pd.to_datetime(scores["start_time"], utc=True).unique())
    with open("prompt_times_BTC.json", "w") as f:
        json.dump([t.isoformat() for t in times], f)
    EOF
    ```
- **Start generating one horizon before the evaluation window.** Otherwise the earliest scored
  prompts have no prediction, and the candidate is backfilled as a late joiner.
- **Always use 1000 paths for numbers you report.** Empirical CRPS is biased upward at fewer paths,
  and the field is scored at 1000.

## The backtester

`synth_lib.backtester` replays the validator's **own** scoring code against the archived field,
with your model injected as UID 999:
- CRPS and the volatility term;
- prompt scores with the cap and the p95 miss fill;
- the window mean, the softmax and reward weights.

Two rule changes follow each prompt's `scored_time`: the outlier cap (from 2026-09-04 09:45 UTC)
and the `crypto-1h` volatility term (from 2026-09-22 13:16 UTC). Older changes are not replayed.

```bash
# one asset in one competition, a settled 3-day window inside the predictions generated above
uv run synth_lib/backtester/scripts/run_backtest.py --miner-name my_model --competition crypto-24h \
  --asset BTC --days 3 --eval-end 2026-10-15 --predictions-dir miner_outputs/my_model/predictions
# a longer, reproducible window: --days > 3 builds or reuses an offline bundle automatically
uv run synth_lib/backtester/scripts/run_backtest.py --miner-name my_model --competition crypto-1h \
  --days 20 --eval-end 2026-10-20 --predictions-dir miner_outputs/my_model/predictions
# model the new-miner ramp-up (backfilled misses before registration); the predictions must cover
# the period from the registration date on
uv run synth_lib/backtester/scripts/run_backtest.py --miner-name my_model --competition crypto-1h \
  --simulate-registration 2026-10-01 --eval-end 2026-10-20 --predictions-dir miner_outputs/my_model/predictions
# build a bundle explicitly, then run against it with no live API calls, passing the bundle's
# --days and --eval-end to every run
uv run synth_lib/backtester/scripts/build_offline_bundle.py --competition crypto-24h --days 30 --eval-end 2026-10-20
SYNTH_BACKTESTER_OFFLINE_DATA_ROOT=offline_data/crypto-24h uv run synth_lib/backtester/scripts/run_backtest.py \
  --miner-name my_model --competition crypto-24h --days 30 --eval-end 2026-10-20 \
  --predictions-dir miner_outputs/my_model/predictions
```

- **Prediction files.** Either use the day layout written by `generate_predictions`, or one JSON
  file per prompt named `YYYY-MM-DD_HH:MM:SSZ_<ASSET>_<time_length>.json`, for example
  `2026-10-01_12:03:00Z_BTC_86400.json`, containing:

  ```
  {"start_timestamp": <int>, "asset": "BTC", "time_increment": 300, "time_length": 86400,
   "num_simulations": 1000, "num_steps": 288, "paths": [[...289 prices...], ...]}
  ```

- **Matching.** A scored prompt is answered by the **latest prediction at or before its start**,
  no more than 60 minutes earlier, and never by a later one.
- **Always pass `--predictions-dir`.** When no predictions are found, the script silently falls back
  to random 100-path predictions (it logs `falling back to random predictions`) and still prints
  ranks. Those ranks mean nothing; grep the log for that line before trusting a number.
- **One command per candidate.** synth-lib's scaffold evaluator answers the scored prompts at their
  real start times with your `simulate()` at 1000 paths, then backtests them against the bundle.
  - It reads prices from `market_data/` beside its `agent/` folder; they must reach 7 days before
    the first prompt.
  - It stops when `SYNTH_BACKTESTER_OFFLINE_DATA_ROOT` is unset.

  ```bash
  mkdir -p agent && cp champions/my_model/modeling.py synth_lib/benchmark/scaffold/workspace/agent/predict.py agent/
  SYNTH_BACKTESTER_OFFLINE_DATA_ROOT=offline_data/crypto-1h uv run agent/predict.py \
    --asset BTC --days 10 --eval-end 2026-10-20 --time-length 3600 --time-increment 60
  ```
- **Output.** Per asset, the script prints `rank: R/N  reward_weight: …  smoothed_score: …` and
  `Prompts scored: N  Mean CRPS: …  realized coverage: P%`. Charts go to
  `miner_outputs/<name>/charts/`: rank evolution, CRPS over time, by hour and by day, CRPS relative
  to the median, and estimated earnings.
- **Warm-up rows are trimmed.** The first `window_days` of smoothed scores are dropped, so a 30-day
  24h window gives about 20 ranked days.
- **From Python.** Use `from synth_lib.backtester.orchestration import backtest, run_backtest`. The
  README's `synth_lib.backtester.backtest` module path is stale.
  - `backtest(..., latest_formula=True)` rescores older `crypto-1h` prompts with the volatility
    term.
- **Treat estimated USD as rough.** The synth-subnet version synth-lib pins may predate the current
  competition split and burn. Compare CRPS and ranks, not dollars.

## Diagnosing `vol_crps`

`vol_crps` is the volatility part of a `crypto-1h` CRPS, already included in `crps`. Its absolute
size says little: on 2026-10-26, one UID's BTC prompts had a median `vol_crps` of 3.9 against a
median `crps` of 541.

- **Compare with the field on the same prompts.** Fetch `/validation/scores/historical` for one
  asset and day without `miner_uid`, take the median `vol_crps` per `scored_time`, and set your
  excess over it against your `prompt_score`.
- **Split a candidate's CRPS.** The backtester prints only the total. Score the candidate's paths
  with the validator's own function, in synth-lib's venv:

  ```python
  from synth.validator.competition_config import CRYPTO_1H
  from synth.validator.crps_calculation import calculate_total_score_for_miner

  # paths: (1000, 61) simulated prices; real: the 61 realised closes from start_time on
  total, rows = calculate_total_score_for_miner(
      paths, real, 60, CRYPTO_1H.scoring_intervals, CRYPTO_1H.vol_scoring_blocks
  )
  vol = next(row["CRPS"] for row in rows if row["Interval"] == "Vol")
  ```

  - The `Vol` row is what the API reports as `vol_crps`.
  - The `vol_60min`, `vol_15min` and `vol_5min` rows hold unweighted sums, with their weight
    beside them.
  - Calling the validator's code is not reimplementing CRPS.
- **Check calibration** with `block_volatilities(paths, block_steps)` from the same module; pass the
  realised path as a one-row array. For each block size, the share of simulated path volatilities
  below the realised one should average about 0.5, and fall outside [0.05, 0.95] on about 10% of
  prompts. A much larger share means the fan of path volatilities is too narrow.
- **The usual cause** is one volatility for every path: path volatilities then differ only by
  sampling noise. Give each path its own volatility level and clustering, then recheck the level
  and the hour-of-day pattern.

## Rules that keep results honest

1. **Score only through synth-lib's backtester,** or the validator's own `crps_calculation`
   functions for a breakdown. Never reimplement CRPS; one implementation is what makes numbers
   comparable.
2. **No lookahead.**
   - End contexts at `context_end`.
   - For an out-of-sample claim, treat everything after the data cutoff as nonexistent, including
     API queries for scores or rewards after it. Use an offline bundle built up to the cutoff.
   - Fit anything learned offline, such as hour-of-day tables, volatility of volatility, or a
     half-life chosen by a sweep, only on data that ends before the window's first prompt, and
     refit for each window. A parameter fitted on the window it is scored on flatters the
     candidate.
   - Keep one recent window sealed. Choose the final candidate on the other windows, then score
     the sealed one once and report that number.
3. **Pin `--eval-end` to a day whose prompts have all settled.** `build_offline_bundle` requires
   every prompt's horizon to have settled and defaults to today − 2 days. Recent prompts are not
   scored yet, and today's price partition is incomplete. Keep the same `--eval-end` across runs
   you compare.
4. **Compare like with like:**
   - same window and `--eval-end`;
   - 1000 paths;
   - equal `realized coverage`, because missing minutes are dropped from the CRPS sum and a thinner
     reference flatters a run.
5. **Do not compare field-relative numbers across structural breaks.**
   - Ranks never carry across them.
   - CRPS carries across the split and the outlier cap, but `crypto-1h` CRPS does not carry across
     2026-03-11 (API values) or 2026-09-22. The backtester adds the volatility term only to prompts
     scored after that date; pass `backtest(..., latest_formula=True)` to compare across it.

   The breaks:
   - 2026-03-11: the `crypto-1h` formula changed.
   - 2026-06-23: the split into 3 competitions.
   - 2026-07-16: the p90 cap removed and misses filled with p95. The backtester does not replay the
     p90 era.
   - 2026-07-22 to 2026-08-03: SPYX replaced by SP500 in `com-equ-24h`; the pinned config knows
     only SP500.
   - 2026-09-04 09:45 UTC: the outlier cap.
   - 2026-09-22 13:16 UTC: the `crypto-1h` volatility term.
6. **Validate on at least two windows of opposite regimes.** Count the asset × window cells where
   a change helps. A small gain with the same sign in most cells is real; a larger one that flips
   sign is noise.
7. **Seed all randomness** from `SYNTH_BENCHMARK_SEED`, with a fallback, and set `PYTHONHASHSEED`.
   Unseeded runs cannot be compared.

## The iteration loop

1. **Write the hypothesis and a kill threshold before running anything,** e.g. "−0.5% CRPS on BTC
   and ETH 24h, or drop it".
2. **Change one thing.**
3. **Generate at 1000 paths and backtest** through synth-lib.
4. **Keep or discard on CRPS,** per asset.
5. **Journal it** as Hypothesis / Change / Result (numbers) / Verdict / Lesson, and commit. The
   journal is your memory if a session dies.
6. **Measure latency early.** A 10-day `crypto-1h` window is about 1,440 scored prompts per asset:
   at 5 s a prompt, that is 2 h per asset and candidate. Keep `simulate()` well under a second at
   1000 paths. Live, the whole answer must arrive within the deadline, with several prompts per
   minute.

Stop when the journal shows that further iteration is not paying off, not when ideas run out.

## Studying the field

**Allowed and useful:** aggregate statistics from the public API (see
[api-and-monitoring.md](api-and-monitoring.md)), for example:
- field CRPS levels per asset and hour;
- how far you are from the best miner on each prompt;
- realised paths for checking your anchor.

**Not allowed:** copying, replaying or reselling any individual miner's served paths.

## Shipping a model

- **With synth-lib serving** ([deployment.md](deployment.md) §D), the backtested `simulate()` is
  served as is. Re-run the contract gate and the real-store pre-flight first.
- **With the stock miner,** put the model in your own module behind a `Miner` subclass
  ([protocol.md](protocol.md), Option 2), not in `synth/miner/simulations.py`. You then own the
  market-data plumbing: the stock miner only fetches a spot price.
- **After deploying,** compare live numbers with the backtest:
  - `/validation/miner` for validity;
  - `/validation/scores/historical` for CRPS, prompt score and `vol_crps` per prompt;
  - first scores arrive after one horizon.

  A model that backtests well but is mis-calibrated live usually has lookahead in its backtest, or
  treats filled minutes as real.

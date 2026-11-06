# Scoring and rewards

Contents:
- The pipeline
- CRPS per prompt
- The `crypto-1h` volatility term
- From CRPS to a prompt score
- Which prompts are scored, and when
- The rolling score per competition
- From scores to weights
- Reading your numbers
- Stale docs to ignore
- Changes that mattered to miners

Source files on synth-subnet `main` (code beats the README):
- `synth/validator/crps_calculation.py`
- `synth/validator/reward.py`
- `synth/validator/moving_average.py`
- `synth/validator/competition_config.py`
- `synth/validator/forward.py`
- `synth/validator/miner_data_handler.py`

## The pipeline

```
prompt → every UID answers (or misses) → horizon ends + 90 s → realized path (1-min closes)
→ CRPS per miner → prompt scores (cap, miss fill, minus best)
→ per competition: asset-weighted mean over the window → softmax → reward_weight
→ sum over competitions → owner burn → set_weights on chain
```

## CRPS per prompt

- **What is scored.** The validator scores price **changes** in basis points,
  `(p[t+k] − p[t]) / p[t] × 10 000`.
  - For each interval and each step, it takes the ensemble of 1000 simulated changes and the
    realized change.
  - It computes the CRPS of each one with `properscoring.crps_ensemble` and sums them all.
- **CRPS itself** is `(1/N) Σ|y_n − x| − (1/(2N²)) ΣΣ|y_n − y_m|`, where `x` is the realized value
  and `y` the ensemble. It rewards calibration and sharpness together.

| Competition                 | Intervals                                                                                          | Terms per prompt                                              |
| --------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `crypto-24h`, `com-equ-24h` | `5min`, `30min`, `3hour`, `24hour_abs`                                                             | 288 + 48 + 8 changes + 1 absolute = 345                       |
| `crypto-1h`                 | `1min`, `2min`, `5min`, `15min`, `30min`, `60min_abs`, plus 12 gaps `0_5min_gaps` … `0_60min_gaps` | 60 + 30 + 12 + 4 + 2 + 1 + 12 = 121, plus the volatility term |

- **`_abs` terms.** They score the absolute price at the horizon. That CRPS is divided by the
  realized final price and multiplied by 10 000. This is the only term where the price level
  itself matters.
- **`_gaps` terms** (`crypto-1h` only). They score the change from t0 to t0 + k minutes. Together
  with `5min`, `15min` and `30min`, the cumulative return from t0 is scored several times, so a
  well-calibrated early spread matters. Like every change term, they are relative to each path's
  own `path[0]`, so the anchor's level does not enter them; only the `_abs` term depends on it.
- **Missing realized minutes** (NaN) are skipped: only observed stretches are scored.
- **Automatic misses.** Any simulated price exactly 0, or a non-finite total, gives the sentinel
  −1, which counts as a miss.

## The `crypto-1h` volatility term

Since 2026-09-22 13:16 UTC, every `crypto-1h` score has had a second part:

```
score = price CRPS + 5.25 × vol CRPS
      = price CRPS + 1.75·S60 + 0.4375·S15 + 0.1458·S5
```

- **Blocks.** The hour is cut into one 60-minute block, four 15-minute blocks and twelve 5-minute
  blocks. `S60`, `S15` and `S5` are the summed CRPS of those three block sizes.
- **Per block,** the sample std (ddof = 1) of the 1-minute bps returns is computed for each
  simulated path and for the realized path. The ensemble of path volatilities is then scored
  against the realized one.
- **Blocks with fewer than 2 observed returns are skipped.**
- **Consequence.** A model with the right price spread but the wrong minute-to-minute roughness
  now loses score: too smooth, too noisy, or no clustering. The 24h competitions have no
  volatility term.

## From CRPS to a prompt score

Per prompt, across all miners (`compute_prompt_scores`):

1. **Cap.** Raw valid CRPS above **10× the median** valid CRPS is capped at that value (live since
   2026-09-04).
2. **Fill misses.** Misses and invalid answers (−1) are filled with the **95th percentile** of the
   valid, uncapped scores.
3. **Subtract the best.** The lowest score is subtracted from every score, so the best miner on the
   prompt gets `prompt_score = 0`.

Consequences:
- An absurd answer (capped at 10× median) scores worse than a miss (p95 of real answers).
- A miss still scores near the bottom. Answer every prompt, and answer well.
- Only the gaps between miners matter. A hard market raises everyone's CRPS, not your rank.

## Which prompts are scored, and when

- **Density tapering.** Each validator keeps **one prompt per asset per hour** (24h competitions)
  or **per 10 minutes** (`crypto-1h`). The other prompts' predictions are dropped before scoring.
  - The kept prompt is chosen afterwards by a salted hash, so you cannot know which one counts.
  - That works out to about 24 scored prompts per asset per day for 24h, and about 144 for 1h.
- **Settlement.** A prompt can be scored once `start_time + time_length + 90 s` has passed and the
  venue has a closed candle after the window. Missing data delays it, and it is retried later.
- **First scores** appear about 1–2 h after a `crypto-1h` answer and about 25–27 h after a 24h
  answer.
- **Every UID is queried for every prompt.** A miner that is down still gets rows, all of them
  misses.

## The rolling score per competition

```
smoothed_score(miner) = Σ prompt_score × c(asset) / Σ c(asset)      over the miner's prompts in the window
```

- **Window:** 5 days for `crypto-1h`, 10 days for `crypto-24h` and `com-equ-24h`. Each competition
  reads only its own assets and `time_length`.
- **Asset coefficients** (`ASSET_COEFFICIENTS` in `moving_average.py`):

  | Asset | Coefficient | Asset | Coefficient | Asset  | Coefficient |
  | ----- | ----------- | ----- | ----------- | ------ | ----------- |
  | BTC   | 1.000       | SP500 | 3.438       | GOOGLX | 1.683       |
  | ETH   | 0.706       | NVDAX | 1.603       | XRP    | 0.566       |
  | XAU   | 1.737       | TSLAX | 1.607       | HYPE   | 0.478       |
  | SOL   | 0.631       | AAPLX | 2.092       | WTIOIL | 0.848       |
  | SPCX  | 0.433       |       |             |        |             |

  A badly modelled high-coefficient asset (SP500, AAPLX, XAU) hurts most in `com-equ-24h`.
  Skipping an asset fills all its prompts with miss scores, weighted by its coefficient.
- **New miners.** A miner whose first score comes after the start of the window is **backfilled**
  with the miss score (`percentile95 − lowest_score` of each prompt) for every earlier scored
  prompt in the window.
  - Rewards therefore ramp up over about 5 days (`crypto-1h`) or about 11 days (24h
    competitions, whose first score itself takes a day).
  - A miner's identity is (UID, coldkey, hotkey). Re-registering, or swapping the hotkey, starts a
    new identity and a new ramp-up.
- **Missing miners.** A miner that misses every prompt still gets a row: its misses carry the p95
  fill, so its smoothed score is poor and its weight near zero. A UID has no row only when it has
  no scored prompt in the window yet, or when its (UID, coldkey, hotkey) identity was replaced.

## From scores to weights

- **Softmax per competition.**
  `reward_weight = softmax(β × smoothed_score) × SMOOTHED_SCORE_COEFFICIENT`.
  - β is −0.3 for `crypto-1h` and −0.15 for the 24h competitions.
  - Only score differences matter. Being 1 point lower than a rival multiplies your weight relative
    to theirs by e^0.15 ≈ 1.16 (24h) or e^0.3 ≈ 1.35 (1h).
  - How concentrated rewards are depends on the spread of smoothed scores, not on β alone. On
    2026-10-27 the field was dense:
    - in `crypto-24h` the top 5 UIDs held about 7% of the slice, and the median UID earned about a
      fifth of the top UID's weight;
    - in `crypto-1h` the median UID earned about half of the top UID's weight.

    Read the live split from the leaderboards ([api-and-monitoring.md](api-and-monitoring.md)).
  - Because the field is dense, small CRPS changes reorder many places.
- **Competition shares.** Each competition is worth `SMOOTHED_SCORE_COEFFICIENT = 1/4` of the miner
  share. The fourth quarter is Synth Ultra, split equally across its assets.
  - That holds only on validators that blend Ultra (`VHFT_SCORES_URL` set).
  - `set_weights` normalises, so a validator without Ultra gives the three on-subnet competitions
    1/3 each.
- **Sum per miner.** A miner's weights from all competitions are added together, so a miner can
  specialise. Missing every prompt of one competition costs only that competition's share.
- **Owner burn.** After the competitions are combined, the owner UID (248 on mainnet) gets a fixed
  fraction of every validator's weights.
  - The fraction is `OWNER_BURN` in `synth/validator/forward.py`; the CHANGELOG announces changes.
  - Miners share the rest.
- **On chain.** Weights are normalised, converted to uint16 and set on chain after each scoring
  round. Your on-chain incentive is the stake-weighted consensus of all validators' weights.
  Realised emission is flatter than one validator's raw weights.

## Reading your numbers

| Field (public API) | Meaning                                                                                                                                                                                                                                                                |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `crps`             | Raw total CRPS of one prompt. `-1` means a miss or an invalid answer                                                                                                                                                                                                   |
| `prompt_score`     | After the cap, the miss fill and minus-best. `0` is the best on that prompt; lower is better                                                                                                                                                                           |
| `vol_crps`         | The volatility part already included in `crps`. `null` without a volatility term (24h, or `crypto-1h` before 2026-09-22)                                                                                                                                               |
| `smoothed_score`   | Your windowed mean per competition. Lower is better                                                                                                                                                                                                                    |
| `reward_weight`    | Softmax weight × `SMOOTHED_SCORE_COEFFICIENT`: your fraction of the whole miner block from that competition, before the owner burn. A competition's values sum to 0.25; they summed to 1/3 before 2026-10-21 14:00 UTC, so older rows are 4/3 larger. Higher is better |

Compare `crps` only within the same asset and prompt. Volatility differs a lot between assets and
between days.

## Stale docs to ignore

| Doc claim                                                     | Current code                                                                                    |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Misses get the 90th percentile; scores capped at p90          | Misses get the p95 of valid scores; raw CRPS capped at 10× the median (since 2026-07 / 2026-09) |
| Each competition is 1/3 of emissions; no burn                 | 1/4 with Synth Ultra blended, plus an owner burn                                                |
| "You'll always have at least 1 minute"                        | 1h prompts: about 50–60 s. 24h prompts: about 110–120 s                                         |
| Smoothed score is an EMA                                      | A coefficient-weighted mean over a fixed window                                                 |
| The best miner's "CRPS is 0"                                  | Only the prompt score is 0. CRPS itself is not                                                  |
| "Use all prompt parameters except sigma"                      | There is no `sigma` in the prompt                                                               |
| `--neuron.timeout` and the `--wandb.*` flags affect the miner | They have no effect on a miner                                                                  |
| Cycle periods of ~15/~15/~32 min                              | The cadence is a validator setting and changes; it is announced in the CHANGELOG                |

## Changes that mattered to miners

From `CHANGELOG.md` and the code history; the 2026-09-04 and 2026-10-21 rows are not in the
CHANGELOG. Read the newest entries yourself: this list ends when the skill was written.

| Date       | Change                                                                                                                                                                        | Miner action                                 |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| 2025-11    | New response format, 1000 paths, windowed smoothed score with per-asset weights                                                                                               | Required                                     |
| 2026-01    | Tokenised equities in the 24h prompt (NVDAX, TSLAX, AAPLX, GOOGLX; SPYX, later retired)                                                                                       | Support the assets                           |
| 2026-04    | XRP, HYPE and WTIOIL added                                                                                                                                                    | Support the assets                           |
| 2026-06-23 | Rewards split into 3 competitions; XAU leaves 1h, XRP joins 1h, SPCX joins com-equ; signed-request blacklist                                                                  | Required: update the blacklist and restart   |
| 2026-07    | 1h deadline cut to about 50 s; p90 cap removed, misses get p95; 8-significant-digit rule                                                                                      | Only if slow, or if you send bools or NaN    |
| 2026-07-22 | Pyth removed: Binance (BTC, ETH, SOL, XRP) and Hyperliquid (the rest); SPYX → SP500 (xyz:SP500, about 10× SPYX's price); `BINANCE_API_HOST`                                   | Required if you used Pyth or SPYX            |
| 2026-08-03 | Prices outside float32 rejected                                                                                                                                               | Keep prices in range                         |
| 2026-09-04 | Raw CRPS capped at 10× the prompt median                                                                                                                                      | None                                         |
| 2026-09-22 | `crypto-1h` volatility term (λ = 5.25); responses gzipped                                                                                                                     | Calibrate minute-level volatility            |
| 2026-10-19 | Owner burn restored; Synth Ultra adds ETH, SOL and XRP (paid from 2026-10-20)                                                                                                 | None for subnet miners                       |
| 2026-10-21 | `SMOOTHED_SCORE_COEFFICIENT` 1/3 → 1/4: every stored `reward_weight` drops by 25% with no change in emission share; Synth Ultra's quarter is split across its rewarded assets | None. Do not read the drop as a score change |

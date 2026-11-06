---
name: synth-miner
description: >-
  Set up, deploy, debug and improve a miner on Synth (synthdata.co), Bittensor subnet 50 (SN50;
  testnet netuid 247), where miners forecast asset prices as 1000 simulated paths scored by CRPS.
  Covers wallets and registration, serving the axon (PM2, Docker, Terraform, Ansible, or synth-lib's
  serving shell), plugging a model into generate_simulations or a synth-lib simulate(), validator
  errors such as "Number of time points is incorrect" or "process time is None", scores, ranks and
  rewards in crypto-1h, crypto-24h and com-equ-24h, backtesting with synth-lib, checking a UID on
  api.synthdata.co, and the separate Synth Ultra (VHFT) competition. Use it whenever the user
  mentions SN50, subnet 50, synthdata, synth-subnet, synth-lib, synth-ultra, a Synth miner, UID or
  leaderboard, or Synth Ultra, even if they do not say "miner". Not for other Bittensor subnets,
  running a Synth validator, using Synth's forecasts as a data customer, or simulation code
  unrelated to Synth.
---

# Running a Synth miner

## The job

Synth pays miners for probabilistic price forecasts. A validator sends a prompt (asset,
`start_time`, `time_increment`, `time_length`, `num_simulations` = 1000). The miner must answer
**before `start_time`** with 1000 simulated price paths. Once the horizon has passed, the validator
scores the whole fan with CRPS against realized one-minute closes. Lower is better.

Synth does not ask for a point forecast. It asks for a calibrated distribution: an overconfident
fan and an over-wide fan both lose. Rewards are a softmax over a rolling mean of per-prompt scores,
so they fall with rank. How steeply depends on how spread out the field is; check the live
leaderboard rather than assuming.

Two things decide whether a miner earns:

1. **Plumbing.** Every prompt is answered on time with a valid response. Every miss is scored, and
   a miss costs a lot.
2. **Model quality.** CRPS per asset and per competition, measured with the official backtester.

Get the plumbing right first, then iterate on the model.

## Competitions

| Competition   | Assets                                                | `time_increment` / `time_length` | Points per path | Score window | Softmax β |
| ------------- | ----------------------------------------------------- | -------------------------------- | --------------- | ------------ | --------- |
| `crypto-1h`   | BTC, ETH, SOL, XRP, HYPE                              | 60 / 3600                        | 61              | 5 days       | −0.3      |
| `crypto-24h`  | BTC, ETH, SOL, XRP, HYPE                              | 300 / 86400                      | 289             | 10 days      | −0.15     |
| `com-equ-24h` | XAU, SP500, NVDAX, GOOGLX, TSLAX, AAPLX, WTIOIL, SPCX | 300 / 86400                      | 289             | 10 days      | −0.15     |

- A competition is identified by **(asset, `time_length`)**, never by the asset alone. BTC at 3600 s
  is `crypto-1h`, and BTC at 86400 s is `crypto-24h`.
- `crypto-1h` also scores the realized volatility of the paths (λ = 5.25). See
  [references/scoring.md](references/scoring.md).
- Synth Ultra is a fourth competition with a different setup: you submit a Docker image, there is no
  axon, and the forecast is 100 percentiles 10 s ahead. See
  [references/synth-ultra.md](references/synth-ultra.md).
- Assets change over time (SPYX was replaced by SP500 in 2026-07). Before relying on this table,
  re-read `synth/validator/competition_config.py` and the top of `CHANGELOG.md`.

## Route the request

| The user wants to…                                               | Read                                                                                                  |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Start mining from scratch (wallet, registration, hardware)       | [references/setup.md](references/setup.md), then [references/deployment.md](references/deployment.md) |
| Deploy, update or supervise a miner (PM2, Docker, VM, synth-lib) | [references/deployment.md](references/deployment.md)                                                  |
| Plug a model into the miner, or fix response-format errors       | [references/protocol.md](references/protocol.md)                                                      |
| Understand scores, ranks, rewards or emissions                   | [references/scoring.md](references/scoring.md)                                                        |
| Improve the model (data, backtests, iteration)                   | [references/model-and-backtesting.md](references/model-and-backtesting.md)                            |
| Check a UID's status or query the field                          | [references/api-and-monitoring.md](references/api-and-monitoring.md), `scripts/miner_status.py`       |
| Fix something broken                                             | [references/troubleshooting.md](references/troubleshooting.md)                                        |
| Join Synth Ultra                                                 | [references/synth-ultra.md](references/synth-ultra.md)                                                |

## Ground rules

- **Keys stay with the human.**
  - Never print, log, store or ask for a mnemonic, seed, password or coldkey.
  - Never read or copy a hotkey file: it is plaintext JSON holding the hotkey's mnemonic and
    private key.
  - The human creates and funds the wallet and registers it, on a trusted machine.
  - The miner host needs only the hotkey file and `coldkeypub.txt`.
  - The agent may run commands that use only public data:
    - `btcli wallet regen-coldkeypub --ss58 …` and `regen-hotkeypub --ss58 …`;
    - `btcli wallet overview`;
    - PM2 and the miner itself.
  - The agent never runs `btcli wallet create`, `new-coldkey`, `new-hotkey`, `regen-coldkey` or
    `regen-hotkey`, and never passes `--mnemonic`, `--seed` or `--json`. When one of those is
    needed, the user runs it in their own terminal on the trusted machine, outside the agent
    session. Never route it through the agent's shell or Claude Code's `!` prefix: both put the
    command and its output, including any printed mnemonic, into the conversation.
- **Money and outside-facing actions need confirmation.** Registration burns TAO, cannot be undone,
  and its price moves.
  - Read the live price (see [references/setup.md](references/setup.md)) and get an explicit yes
    before the user registers.
  - Do the same before `terraform apply`, before opening ports on a remote host, and before
    restarting a miner that is earning.
- **Rehearse offline first.** Use `scripts/check_responses.py` or synth-lib's checks, and the
  synth-lib backtester.
  - Testnet (netuid 247) can rehearse registration, ports and startup.
  - Validator traffic on testnet is not guaranteed: the miner blacklist only answers validators
    staking more than 65000 there too.
- **Do not copy other miners.** Studying the field in aggregate through the public API is fine.
  Replaying or reselling another miner's served paths is not.
- **Be gentle with the public APIs.**
  - They are rate-limited: synth-lib's README says a couple of requests per second.
  - Space requests out (`miner_status.py` waits about a second between calls), back off on
    HTTP 429, and always pair `time_length=3600` with `time_increment=60`.
- **Code beats docs.** Some figures in the README and FAQ are stale: the "90th percentile" penalty,
  "1/3 per competition", "at least 1 minute to answer" and "0.25 TAO is enough".
  - Trust synth-subnet `main` and the newest `CHANGELOG.md` entries.
  - Read every new CHANGELOG entry. The "Miner action required" tag is not applied consistently.
- **The pinned btcli can lag the chain.** On 2026-10-27, bittensor-cli 9.22.3 failed some reads
  against the chain runtime with `Storage function … not found`, and 9.23.2 worked. Use the SDK
  one-liners in [references/setup.md](references/setup.md), or a newer bittensor-cli.

## What happens after registration

1. **Register.** The hotkey gets a UID right away, and its immunity period starts. The subnet owner
   sets its length: 65535 blocks, about 9.1 days, on 2026-10-27. Read it live with the one-liner in
   [references/setup.md](references/setup.md).
2. **Start the miner.** On startup, `axon.serve` publishes the miner's external IP and port on chain.
3. **Wait for validators to see it.** Validators re-read axon addresses about every 15 minutes, so
   the first requests can take that long. The same applies after any IP or port change.
4. **Every UID is queried for every prompt.** An unreachable or slow miner is scored as a miss.
5. **First scores** appear about 1–2 h after a `crypto-1h` answer and about 25–27 h after a 24h
   answer.
6. **Ramp-up.** A new hotkey is backfilled with miss-level scores for everything in the window
   before it joined.
   - `crypto-1h` clears in about 5 days; the 24h competitions take about 11 days.
   - That outlasts a 9.1-day immunity, so newcomers are most at risk of being pushed out between
     day 9 and day 11.
   - Be live and answering from the first block, and re-registering starts all of this over.

## The response contract

Most failing miners break one of these rules. Details and error strings are in
[references/protocol.md](references/protocol.md).

- **Shape.** Return `(start_ts, time_increment, path_1, …, path_N)`.
  - `start_ts` is `int(datetime.fromisoformat(simulation_input.start_time).timestamp())`; the
    string already carries `+00:00`.
  - `time_increment` is the prompt's value, as an `int`.
  - `N` is `simulation_input.num_simulations` (1000 in production). Never hard-code it.
- **Paths.** Each path is a list of `time_length // time_increment + 1` prices (61 or 289), and
  `path[0]` is the price at `start_time`.
- **Prices.** Every price must be:
  - finite: a NaN or inf makes the axon answer HTTP 500;
  - strictly positive: a single 0 turns the whole response into a miss at scoring;
  - inside float32 range;
  - at most 8 significant digits. As the very last step, round in float64 with
    `float(f"{p:.7e}")`. A float32 value converted back to a Python float has too many digits.
- **Deadline.** The prompt's `start_time` is the deadline: about 50–60 s after the request for 1h
  prompts and about 110–120 s for 24h prompts.
  - Several 1h prompts and a 24h prompt can arrive from each validator in the same minute.
  - The stock `forward_miner` runs model code on the axon's event loop, so it answers them one
    after another. Keep each answer to a few seconds.
- **Check before deploying.**
  - Stock miner: `scripts/check_responses.py` covers every competition shape at 1000 paths and
    fails any call slower than 5 s.
  - synth-lib serving: use the checks in [references/deployment.md](references/deployment.md) §D.
  - `python synth/miner/run.py` only checks BTC 24h with 100 paths.

## Scoring essentials

Full details are in [references/scoring.md](references/scoring.md).

- **CRPS per prompt.** Each prompt's CRPS is a sum over intervals of basis-point price changes:
  - 24h: 5 min, 30 min, 3 h, plus the absolute 24 h price;
  - 1h: 1, 2, 5, 15 and 30 min, the absolute 60 min price, and 12 "gap" changes measured from t0;
  - 1h also adds the volatility term.
- **Prompt score.** Raw CRPS is capped at 10× the prompt's median. A miss or invalid answer gets
  the 95th percentile of the valid answers. The best score is then subtracted, so the best miner
  gets 0 on that prompt.
  - An absurd answer scores worse than a miss, and a miss still scores near the bottom.
- **Which prompts count.** Only one prompt per asset per hour (24h competitions), or per 10 minutes
  (`crypto-1h`), is scored. The validator picks it afterwards with a salted hash, so answer every
  prompt.
- **Leaderboard.** Each competition takes an asset-weighted mean of prompt scores over its window,
  then a softmax.
  - Small CRPS differences reorder many places.
  - Optimise CRPS per asset, and read rank only as an outcome.
- **Emissions.** A miner's weights from all competitions are added together. Competitions are
  independent, so a miner can specialise.
  - On a validator that blends Synth Ultra, each competition is worth 1/4 of the miner share;
    otherwise each is worth 1/3.
  - An owner burn takes a fixed share of the weights. `OWNER_BURN` is in
    `synth/validator/forward.py`, and `CHANGELOG.md` has the current value.

## Quick diagnosis

| Symptom                                                                             | Most likely cause                                                                                          | First move                                                                                                                                               |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/validation/miner` says `time out or internal server error (process time is None)` | No HTTP 200 before `start_time`: closed port, wrong IP, blacklist, exception, NaN price, or too slow       | In the miner log, look for the request line (stock: `Received prediction request from`; any miner: `Requesting UID:`), then `<ErrorType>#<uuid>:` errors |
| `Number of time points is incorrect: expected 289, got 288`                         | The t0 point is missing                                                                                    | Emit `time_length // time_increment + 1` points                                                                                                          |
| `Price format is incorrect: too many digits …`                                      | No 8-significant-digit rounding, or float32 maths                                                          | `float(f"{p:.7e}")` as the last step                                                                                                                     |
| Process exits: `… is not registered on netuid 1`                                    | `--netuid` omitted (default 1), or the wrong wallet or network                                             | Pass `--netuid 50` (or `247 --subtensor.network test`)                                                                                                   |
| BTC, ETH, SOL and XRP all fail                                                      | api.binance.com returns HTTP 451 in some regions, such as the US                                           | Set `BINANCE_API_HOST=https://data-api.binance.vision` in the miner's environment                                                                        |
| No `Requesting UID:` lines at all                                                   | INFO logging is off (no `--logging.debug`), the miner is unreachable, or validators have not refreshed yet | Check the logging flag; wait 15 min; open TCP 8091 in the host and cloud firewalls; check the published address                                          |
| `stake below minimum threshold of 65000` on testnet                                 | The blacklist enforces a minimum stake of 65000 on every network                                           | See [references/troubleshooting.md](references/troubleshooting.md)                                                                                       |
| Scores are fine but rewards are tiny for days                                       | New-hotkey backfill (ramp-up)                                                                              | Wait it out (about 5 days for `crypto-1h`, 11 for 24h). Do not re-register                                                                               |

## Tools in this skill

Both scripts live in this skill's `scripts/` folder.

- **`miner_status.py`** uses only the standard library and runs anywhere:
  `python3 <skill dir>/scripts/miner_status.py --uid <UID> [--scores]`. It shows:
  - the latest validation result;
  - rank, reward weight and smoothed score per subnet competition;
  - the UID's Synth Ultra rows, if any;
  - with `--scores`, per-asset prompts, misses and mean prompt score since 00:00 UTC yesterday.

  It paces itself to about one request per second.
- **`check_responses.py`** runs from the synth-subnet root inside its venv:
  `PYTHONPATH=. python <skill dir>/scripts/check_responses.py [--function module:function] [--assets BTC,XAU]`.
  - It calls a function with `generate_simulations`' signature for every competition asset at
    1000 paths.
  - It checks each output with the validator's own `validate_responses`, and flags prices ≤ 0 and
    calls slower than 5 s.
  - It exits 1 on any failure.

## Sources of truth

These change. Re-read them when in doubt.

- **synth-subnet** ([github.com/synthdataco/synth-subnet](https://github.com/synthdataco/synth-subnet)):
  - `synth/validator/competition_config.py`, `prompt_config.py`, `response_validation_v2.py`,
    `reward.py`, `moving_average.py`;
  - `neurons/miner.py`, `synth/miner/`;
  - `docs/miner_tutorial.md`, `docs/miner_reference.md`, `miner-setup/`;
  - `CHANGELOG.md`, and the releases feed
    (https://github.com/synthdataco/synth-subnet/releases.atom).
- **synth-lib** ([github.com/synthdataco/synth-lib](https://github.com/synthdataco/synth-lib)):
  market data, the validator-faithful backtester, and the `synth_lib.serving` miner shell. Its
  research method for model campaigns is in `synth_lib/benchmark/constitution/CAMPAIGN.md.tmpl`.
- **Synth API:** https://api.synthdata.co/docs. **Monitoring API:**
  https://monitoring.synthdata.co/v1/openapi.json.
- **Dashboards:** https://synthdata.co/miners/performance and https://taostats.io/subnets/50/metagraph.
- **Community:** the Synth channel on the Bittensor Discord, linked from the synth-subnet README.
- **Synth Ultra:** [github.com/synthdataco/synth-ultra](https://github.com/synthdataco/synth-ultra).

# Synth Ultra

Contents:
- How it differs from subnet mining
- Joining, step by step
- The model contract
- The sandbox and latency
- The payload
- Scoring
- Pitfalls

Synth Ultra (code name VHFT) is a separate, ultra-low-latency competition inside subnet 50. Its only
source of truth is the public **synth-ultra** repo (https://github.com/synthdataco/synth-ultra):
- `README.md`, `ONBOARDING.md`, `SPECIFICATION.md`, `FAQ.md`, `input.md`;
- `client/submit.py`, `client/validate.py`;
- `samples/`.

Re-read `FAQ.md` and `input.md` before acting: limits and the payload schema change.

## How it differs from subnet mining

|              | Subnet competitions                      | Synth Ultra                                                                                                    |
| ------------ | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| What you run | An axon on your own host                 | A Docker image that Synth runs in its own sandbox                                                              |
| Forecast     | 1000 price paths over 1 h or 24 h        | 100 percentiles of the **spot microprice 10 s ahead**                                                          |
| Assets       | 13 assets in 3 competitions              | BTC, ETH, SOL and XRP, each its own competition                                                                |
| Input        | The prompt only; you fetch your own data | A payload of live Binance spot and futures data plus Bybit perp trades; no network access                      |
| Scored by    | The validators, inline                   | Synth's scorer, off-subnet. Validators that blend it give it 1/4 of the miner weights, split across its assets |

One UID can earn from the subnet competitions and from Synth Ultra at the same time.

There is no Synth Ultra testnet or staging service. Testnet netuid 247 runs only the subnet
competitions, and applying needs a mainnet UID. Before the first live deploy, the only checks are
local: `client/validate.py` on the real captures, and the image under `runsc` (gVisor).

## Joining, step by step

1. **Hold a registered SN50 UID on the hotkey** (see [setup.md](setup.md)). Applications from
   hotkeys without one are refused with HTTP 403.
   - The subnet validators query every UID. A hotkey that serves no axon misses every prompt of
     the three subnet competitions, for good, so its emission is its Synth Ultra payout alone.
   - Immunity lasted about 9.1 days on 2026-10-27 ([setup.md](setup.md) reads it live). Approval
     is manual and limited per day, and every submission is reviewed. Register only once the model
     is ready, and be live in Synth Ultra well before immunity ends; otherwise the UID is among the
     first pruned.
2. **Apply.** Every request is signed with the hotkey; the hotkey file must hold its secret. The
   handle becomes your registry repo name (`vhft-<handle>`).

   ```bash
   git clone https://github.com/synthdataco/synth-ultra.git && cd synth-ultra
   uv run --no-project --with "bittensor>=11,<12" python client/submit.py apply \
     --wallet <wallet_name> --hotkey <hotkey_name> --handle <handle> --email <email> --discord <discord>
   ```

   - A person reviews each application, and a limited number are set up per day.
   - Applications are capped per hotkey: apply once, then poll.
   - Response codes:
     - 202: recorded.
     - 403: no SN50 UID.
     - 429: rate-limited, or "already applied".
     - 503: applications are closed.
3. **Collect the push key.** The same command reports the status
   (`pending_review` → `provisioning` → `provisioned`):

   ```bash
   uv run --no-project --with "bittensor>=11,<12" python client/submit.py credentials --wallet <wallet_name> --hotkey <hotkey_name>
   ```

   - It writes `vhft-push-<slug>.json`, a service-account key, with mode 600.
   - There are **two retrievals in total**. Save the file the first time.
   - Keep it out of any git repo: synth-ultra's `.gitignore` covers it, or pass `--out` to a path
     elsewhere.
4. **Log in to your registry repo:**

   ```bash
   cat vhft-push-<slug>.json | docker login -u _json_key --password-stdin https://asia-northeast1-docker.pkg.dev
   ```

5. **Build** for `linux/amd64`, from the base image. Pin it by digest for reproducibility; the
   digest is in `FAQ.md`.

   ```dockerfile
   FROM ghcr.io/synthdataco/vhft-miner-base:v2
   COPY my_model/ /app/my_model/
   ENV VHFT_MINER_ENTRYPOINT=my_model.model
   ```

6. **Validate locally** against the real captures for every asset you answer. `validate.py` checks
   the contract and latency, not accuracy. Run it from the directory that holds `my_model/`:

   ```bash
   PYTHONPATH=. uv run --no-project --with numpy python client/validate.py --entrypoint my_model.model --payload samples/real_payload.json.gz
   PYTHONPATH=. uv run --no-project --with numpy python client/validate.py --entrypoint my_model.model --payload samples/real_payload_ETH.json.gz
   ```

   `python client/validate.py` puts `client/` on `sys.path`, not the working directory, so without
   `PYTHONPATH` the import fails with `ModuleNotFoundError: No module named 'my_model'`.

7. **Push, then submit by digest:**

   ```bash
   docker build --platform linux/amd64 -t <registry-repo>/miner:v1 . && docker push <registry-repo>/miner:v1
   uv run --no-project --with "bittensor>=11,<12" python client/submit.py submit --wallet <wallet_name> \
     --hotkey <hotkey_name> --image-uri <registry-repo>/miner --image-digest sha256:<digest> --version 1
   ```

   - Deployment pins the digest. Pushing a new tag changes nothing until you submit again.
   - There is at most **one accepted submission per hotkey every 4 hours**, so a broken version
     cannot be replaced for 4 hours. Raise `--version` on every update.
   - Nothing public says whether the previous version keeps answering while a new one starts.
     Resubmit for real improvements only.
8. **Track it:**

   ```bash
   uv run --no-project --with "bittensor>=11,<12" python client/submit.py status --wallet <wallet_name> --hotkey <hotkey_name>
   ```

   - `status` is `pending`, `approved`, or `rejected` with a reason.
   - `deploy_state` is one of:
     - `live`;
     - `queued`: no free slot;
     - `not_registered`: the hotkey lost its UID; it deploys once registered again;
     - `pending_review`, `rejected` or `disabled`.
   - `live_digest` is the digest that is actually running.
   - An approved version usually goes live within about 2 minutes.
9. **Read your scores.** `status` reports no scores, and the synth-ultra docs name no page for
   them. The public Synth API's `/rewards/scores` returns per-asset Synth Ultra rows when queried
   without a `competition` filter ([api-and-monitoring.md](api-and-monitoring.md));
   `scripts/miner_status.py --uid <UID>` prints them.

## The model contract

```python
def predict_percentiles(payload: dict) -> np.ndarray:  # shape (100,), dtype float64
    ...
```

- **Percentiles.** Return 100 values on the centred grid `q_i = (2i − 1)/200`, from 0.005 to 0.995.
  They must be finite, strictly positive and non-decreasing.
- **Entrypoint.** `VHFT_MINER_ENTRYPOINT` is `package.module` or `package.module:function`.
- **Same payload, same output.** No randomness without a fixed seed, and no state carried across
  calls that changes the answer.
- **Loading is free.** The module is imported once, before prompts are accepted, and gets one
  discarded warm-up call. Do all loading and preallocation at import time.
- **A raise is a silent non-answer.** `input.md` describes a model that raises as one that stops
  answering while its container stays up and healthy, so no error reaches you. Each such prompt
  counts as a non-answer (see Scoring). Raise on purpose only to skip an asset entirely.

## The sandbox and latency

- **Environment:**
  - a gVisor sandbox with 2 pinned cores and a couple of GB of RAM, on a shared host;
  - a read-only filesystem with a small in-memory `/tmp`;
  - no network and no GPU;
  - an image under 2 GB, built `FROM` the base image, without overriding the serving command.
- **Syscalls are expensive under gVisor.** That includes allocation churn, file I/O and thread
  creation. Keep `predict_percentiles` free of allocation, I/O and threads, and benchmark on a
  single core.
- **Compiled extensions are allowed** when built during the Docker build with their source left in
  the image. Do not ship a prebuilt binary, or a multi-stage build that drops the source.
  - Compile for a conservative baseline instruction set, never `-march=native`.
  - Built on a machine newer than the evaluation host, such code crashes with an illegal
    instruction on the first call, and a test on the build machine does not show it.
- **Latency.** The target is **< 5 ms** per call, measured around `predict_percentiles` inside the
  container.
  - When the FAQ was last checked, what was enforced was a 1-second round-trip timeout. It will
    tighten toward 5 ms, so build for 5 ms.
  - A call over the limit counts as a non-answer.

## The payload

The payload is a dict of NumPy arrays (full reference: `input.md`):
- **`prompt`:** `{asset, horizon_seconds: 10, num_percentiles: 100, quantile_grid, current_time_ms,
  trigger{kind, venue}}`.
- **`venues.spot` / `venues.futures`:**
  - `candles_1s`: 1 h of one-second candles;
  - `trades`: 60 s;
  - `book_ticker`: 60 s;
  - order-book depth: snapshot, updates and latest;
  - `liquidations`: futures only;
  - `depth_bands`: spot only;
  - `last_event_times`.
- **`other_venues.bybit_futures`:** 60 s of Bybit USDT perp trades.

Rules and traps:
- **Schema version.** Treat `schema_version` as a **minimum** (6 or later), and ignore unknown
  keys. Fields are added without notice; removals and renames are announced.
- **Trigger kinds.** Handle the `time` trigger explicitly and treat any other kind as an event: the
  kind values changed once without a schema bump.
- **Timestamps.** `recv_ts_ms` is the canonical clock. On event triggers the triggering trade is in
  `trades`, but `book_ticker` may not reflect it yet, and half of event prompts are delayed by up to
  500 ms.
- **Empty or short arrays.** Futures `candles_1s` can be incomplete (`complete_history: False`),
  `depth_bands` can be empty before the first snapshot, and Bybit trades can be empty.
- **Liquidations.** `is_buy` is the side of the liquidation order: `True` means buying to close a
  short, which pushes the price up.
- **Price scale.** Branch on `payload["prompt"]["asset"]` and scale everything from the payload:
  XRP trades near $1, BTC near $80k. Tick sizes differ too.

## Scoring

- **The score.** Pinball-loss CRPS in **price units** against the Binance **spot microprice**
  `(bid·ask_qty + ask·bid_qty)/(bid_qty + ask_qty)` at `current_time_ms + 10 s`. Futures trade at a
  basis, so forecast spot.
- **Prompt rate.** Each asset is prompted about 4.5–6 times a minute: clock prompts every 20 s,
  staggered by asset, plus move-triggered prompts.
- **Misses.** A non-answer or invalid output costs the **95th percentile** of the models that
  answered, and each prompt's best is subtracted. Scores are averaged over a rolling window of
  about a day, so gaps and failed versions hurt for a while.
- **Assets are separate competitions.** A model with no valid answer for an asset in the window is
  not in that asset's competition at all. To skip an asset, never return a valid answer for it:
  raise, as `input.md`'s own version check does.
- **Weights.** Validators that blend Synth Ultra apply a steep per-asset softmax to the published
  scores. A validator skips the blend for a cycle when the scorer's answer is unusable; that
  cycle's weights then come from the three subnet competitions only.
- **Duplicates.** One operator gets one slot, and a model may run under one hotkey only. Synth
  compares image code and live forecasts, and removes duplicates after review.

## Pitfalls

- **The image is accepted but crashes at once.**
  - Cause: it was built for arm64 (Apple Silicon).
  - Fix: build with `--platform linux/amd64`.
- **The image crashes on the first call with an illegal instruction.**
  - Cause: native code compiled with `-march=native` on a newer machine.
  - Fix: compile for a baseline instruction set.
- **The Synth Ultra score sinks while `status` says live, and nothing reports an error.**
  - Cause: `predict_percentiles` raises, for example on a strict `schema_version ==` check, an
    exhaustive match on `trigger.kind`, or an index into an empty array (Bybit trades,
    `depth_bands`, short futures candles).
  - Fix: guard every possibly-empty array, keep a default branch, and run `validate.py` on every
    real capture. Watch your rows in `/rewards/scores`.
- **`validate.py` stops with a traceback instead of a contract report.**
  - Cause: it does not catch exceptions from `predict_percentiles`. In production the same raise
    is a silent non-answer.
  - Fix: fix the code path that raised.
- **`validate.py` fails with `ModuleNotFoundError: No module named 'my_model'`.**
  - Fix: prefix the command with `PYTHONPATH=.`, from the directory that holds `my_model/`.
- **Fine in `validate.py`, but breaks live on empty fields (or the reverse).**
  - Cause: `validate.py` passes empty JSON lists as Python lists, while the runtime sends
    zero-length arrays.
  - Fix: wrap inputs in `np.asarray`.
- **Fast locally, slow in production.**
  - Cause: gVisor syscall costs, and bigger payloads in busy markets.
  - Fix: preallocate, benchmark on one core with the `real_payload*` captures, and test the image
    under `runsc`.
- **Huge CRPS on ETH, SOL or XRP.**
  - Cause: BTC-scale constants.
  - Fix: scale to each asset's payload prices.
- **A new image is not live.**
  - Fix: submit again with the new digest and a higher `--version`.
- **A score dip around a version change.**
  - Cause: unanswered prompts while the version changes are charged the p95; the switch-over is
    undocumented.
  - Fix: do all heavy setup at import, and avoid needless resubmissions.
- **`deploy_state: not_registered`.**
  - Cause: the hotkey has no SN50 UID, never registered or pruned.
  - Fix: register it again; deployment then follows. The subnet treats the new registration as a
    new identity, so its ramp-up in the subnet competitions restarts.
- **An Ultra-only UID is pruned soon after its immunity ends.**
  - Cause: with no axon it earns nothing in the subnet competitions, and it was not yet live, or
    not yet scoring, in Synth Ultra.
  - Fix: be live in Synth Ultra well inside the immunity period.
- **`apply` returns 403, or no Ultra testnet can be found.**
  - Cause: applications need an SN50 mainnet UID, and there is no Synth Ultra testnet.
  - Fix: register on mainnet, and test locally with `validate.py` and `runsc`.
- **Chain incentive on taostats does not match your Synth Ultra rank.**
  - Cause: on-chain weight mixes every competition and the owner burn.
  - Fix: judge Synth Ultra by its own rows in `/rewards/scores`.
- **`credentials` returns 410.**
  - Cause: both retrievals are used.
  - Fix: ask Synth to re-issue the key.
- **The docs say "submissions are not open yet" or "strictly enforced".**
  - Cause: stale banners. `ONBOARDING.md` and `client/submit.py` describe the live flow, and the
    FAQ gives the enforced latency.

# Troubleshooting

Contents:
- Startup and process
- Registration and keys
- No requests, or every answer times out
- Validation errors
- Prices and market data
- Scores and rewards
- synth-lib serving
- Docker, PM2 and provisioned VMs
- Public API

Work in this order:
1. **Is the process up, on the right netuid, and alive inside?** See "Is it healthy?" in
   [deployment.md](deployment.md).
2. **Do requests reach the miner?**
   - On every path, the inherited blacklist logs `Requesting UID: <uid> | Stake at UID: <stake>` for
     each request from a registered validator that publishes an IP.
   - The stock miner also logs `Received prediction request from <hotkey>`. A miner that overrides
     `forward_miner`, including synth-lib's, does not.
3. **Are answers valid?** Check `/validation/miner`.
4. **Are scores competitive?** Check `/validation/scores/historical`.

Most "my miner earns nothing" reports stop at step 2 or 3.

Every log line this file relies on is an INFO line. Without `--logging.debug`, `--logging.info` or
`--logging.trace`, bittensor logs warnings only. The shipped configs pass `--logging.debug`.

## Startup and process

- **The process exits with `Wallet: … is not registered on netuid 1`, and PM2's restart count
  climbs.**
  - Cause: `--netuid` was omitted (the default is 1), the wallet or hotkey names do not match, or
    the network is wrong (a testnet hotkey without `--subtensor.network test`).
  - Fix: pass `--netuid 50`, or `--netuid 247 --subtensor.network test`, and check
    `btcli wallet overview --wallet.name … --wallet.hotkey …`.
  - The miner exits with status 0, which PM2 restarts too. `stop_exit_codes: [0]` in the PM2 app
    stops the loop.
- **Startup crash: `AttributeError: 'NoneType' object has no attribute 'name'` in `check_config`.**
  - Cause: an exported `BT_NO_PARSE_CLI_ARGS=true`. bittensor then parses no flags at all, so the
    miner's `neuron` config is never built.
  - Fix: unset it in the shell, the PM2 `env` and the compose file.
- **A flag seems to have no effect: wrong port, network or stake floor.**
  - Cause: lenient parsing silently drops misspelled or removed flags. Examples:
    `--blacklist.validator.min_stake`, `--blacklist.force_validator_permit`, `--neuron.timeout`.
  - Fix: compare the config the miner logs at startup with your flags.
  - Do not add `--strict`. The miner parses its flags once without the miner-only options, so it
    then rejects valid flags such as `--blacklist.validator_min_stake` and exits.
- **`pm2 start miner.config.js -- --netuid 247` changes nothing.**
  - Cause: PM2 drops every argument after `--` when it starts from a config file. The docs'
    per-option examples have this problem.
  - Fix: edit `args` in your local config, then
    `pm2 restart miner.local.config.js --update-env && pm2 save`.
- **After `pm2 restart miner -- <flags>` the miner comes back on netuid 1 with the default wallet.**
  - Cause: restarting by name with `--` replaces the whole argument list.
  - Fix: `pm2 delete miner && pm2 start miner.local.config.js && pm2 save`.
- **The miner reaches finney although `--subtensor.chain_endpoint ws://…` was passed.**
  - Cause: with bittensor 10.4.x, the default `--subtensor.network finney` wins over a bare chain
    endpoint.
  - Fix: pass the URL as `--subtensor.network ws://host:9944`, and confirm with the startup line
    `Subtensor: Network: unknown, Chain: ws://…`.
  - The `Serving miner axon … on network:` line only echoes the configured value.
- **`TypeError: issubclass() arg 1 must be a class`, or `The blacklist_fn function must have the
  signature …` at startup.**
  - Cause: `from __future__ import annotations` in the module that defines `forward_miner`, or a
    changed `blacklist` or `priority` signature.
  - Fix: see [protocol.md](protocol.md), "Where to plug a model in".
- **`TypeError: '>' not supported between instances of 'str' and 'int'` at startup.**
  - Cause: `--neuron.events_retention_size` was passed on the command line (the flag is declared as
    a string).
  - Fix: drop the flag.
- **`ModuleNotFoundError: No module named 'synth'` (or `bittensor`) under PM2.**
  - Cause: PM2 was started outside the repo root (`PYTHONPATH: "."` is relative), or `python3`
    resolved to the system Python 3.10.
  - Fix: set an absolute `interpreter` (`<repo>/.venv/bin/python3`) and `cwd` in the PM2 config.
- **PM2 says online, but `Step` in `Miner | Step:` stops increasing.** Stake looks frozen, and new
  validators get `Unrecognized hotkey`.
  - Cause: the background sync thread died. Look just before the freeze:
    - `Unknown error` and a traceback mean an exception, often a chain endpoint error or HTTP 429;
    - an `is not registered on netuid` line with no traceback means the hotkey was deregistered.

    The axon keeps serving with a stale metagraph.
  - Fix: fix the cause, then `pm2 restart miner`. Alert on `Unknown error` and on a `Step` that
    stops advancing.
- **PM2 says online and `Miner | Step:0` keeps printing, but port 8091 is closed.**
  `Miner starting at block:` never appeared, and the error log shows
  `Exception in thread … (run):`.
  - Cause: the first sync failed before the axon started.
  - Fix: read that traceback (often the chain endpoint), fix it and restart.
- **The public chain endpoint returns HTTP 429, or the miner hammers it.**
  - Cause: a miner never sets weights, so after `--neuron.epoch_length` blocks its metagraph
    re-sync runs back to back.
  - Fix: point the miner at your own subtensor node (`--subtensor.network ws://…`), and stagger
    restarts.
- **The miner is gone after a reboot.**
  - Cause: PM2 was never told to restore its processes.
  - Fix: run `pm2 startup` (then the printed sudo command) and `pm2 save`.

## Registration and keys

- **btcli fails with `Storage function … not found`** (for example on `btcli subnets show` or
  `btcli subnets hyperparameters`).
  - Cause: the pinned bittensor-cli 9.22.3 is older than the chain runtime.
  - Fix: use a newer bittensor-cli, or the SDK one-liners in [setup.md](setup.md).
- **`btcli subnet register` fails with `Insufficient balance … Current recycle is … TAO`.**
  - Cause: the registration price moves; it rises with every registration.
  - Fix: read it live (`bt.Subtensor('finney').recycle(50)`) and fund the coldkey above price plus
    fee.
- **`RegistrationPriceLimitExceeded`.**
  - Cause: the price moved more than the 5% safe-registration tolerance before inclusion.
  - Fix: wait for it to decay and retry.
- **`Error: Wallet 'miner' exists but the hotkey 'default' does not` when registering.**
  - Cause: btcli needs the private hotkey file on the registering machine.
  - Fix: create the hotkey there (see [setup.md](setup.md)).
- **`btcli subnets burn-cost` shows a huge number.**
  - Cause: that command prices a new subnet, not a miner registration.
- **Keyfile not found under `/root/.bittensor/…` (or `/home/<user>/…`) although the wallet
  exists.**
  - Cause: btcli and the miner ran as different users.
  - Fix: use the same user, or pass `--wallet.path`.
- **PM2 miner hangs on a password prompt, or logs that the hotkey password is invalid.**
  - Cause: the hotkey was encrypted, and PM2 has no terminal to type into.
  - Fix: keep the hotkey unencrypted, protected by file permissions.
- **Deregistered.**
  - Cause: when the subnet is full, each new registration evicts the lowest-emission UID among
    those past their immunity period (about 9.1 days on 2026-10-27). Newcomers whose 24h ramp-up
    (about 11 days) is not finished are the usual victims.
  - Fix: check `btcli wallet overview`, then the human registers again. The new identity starts its
    ramp-up from zero, so fix the reason the miner scored low first.

## No requests, or every answer times out

`/validation/miner` reason `time out or internal server error (process time is None)` means the
validator did not get an HTTP 200 before `start_time`. Find which case applies:

- **No `Requesting UID:` lines in the log at all.** INFO logging is off, the miner is unreachable,
  or validators have not refreshed yet.
  - Check that the miner runs with `--logging.debug`.
  - Wait 15 minutes after starting.
  - Open TCP 8091 in ufw **and** in the cloud firewall.
  - Check the published address: the AXON column of `btcli wallet overview`, or the SDK one-liner
    in [deployment.md](deployment.md).
  - Behind NAT, set `--axon.external_ip` / `--axon.external_port`.
- **Blacklist lines** (`stake below minimum threshold`, `no published IP on-chain`,
  `non-validator hotkey`). Your blacklist rejected the caller, and the validator scores that as a
  miss. Keep `--blacklist.validator_min_stake` at 65000.
- **An error line `<ErrorType>#<uuid>: <message>` after the request.** The axon answered HTTP 500.
  The traceback itself is logged only with `--logging.trace`.
  - `ValueError#…: Out of range float values are not JSON compliant` means a NaN or inf price.
    Bound the paths and keep every price finite.
  - `cannot convert float NaN to integer` comes from the stock rounding helper on a NaN price.
    Same fix.
  - Anything else means `forward_miner` raised. Fix it, and give the forward a sane fallback
    ([protocol.md](protocol.md)).
- **Requests arrive with no error.** The answer is too slow.
  - Time your generate function at 1000 paths with `scripts/check_responses.py`.
  - Remove network calls from the request path.
  - Offload the work (see [protocol.md](protocol.md)).
- **HTTP 401 `Nonce is too old`.** Either the clock is skewed or the request waited behind a
  blocked event loop. Enable NTP, and do not block the loop.

More cases:
- **Only 1h prompts time out, and 24h prompts pass.**
  - Cause: the 1h deadline is about 50–60 s, the 24h one about 110–120 s. Often the reference price
    fetch or a slow model is to blame.
  - Fix: cache prices in the background and keep compute to a few seconds.
- **Misses come from only some validators.**
  - Cause: a minimum-stake setting above theirs, or a firewall allow-list missing their IP.
  - Fix: derive the allow-list from the chain with `chain_sync.py`.
- **The IP changed after a VM stop and start.**
  - Cause: the IP is published only at miner startup.
  - Fix: restart the miner, wait 15 minutes, and reserve a static IP.
- **An external port checker says 8091 is closed, but validators get through.** That is expected
  once a validator allow-list is active. Judge reachability from `Requesting UID:` lines.
- **The log fills with `UnknownSynapseError: … 'MarketSimulationStateUpdate' …`.** Something is
  sending a synapse that Synth does not use. It is noise, not an outdated miner. Restrict ingress
  to validators with `chain_sync.py`.
- **Testnet: every request is blacklisted with `stake below minimum threshold of 65000`.**
  - Cause: the 65000 floor applies on testnet too, and `miner.test.config.js`'s min-stake flag is
    misspelled.
  - Fix: see [setup.md](setup.md), "Testnet rehearsal".

## Validation errors

Each `reason` string is mapped to its fix in [protocol.md](protocol.md), "Validation messages".
The four common ones:
- `Number of time points is incorrect`: include t0, and use 61 points for 1h and 289 for 24h.
- `Number of paths is incorrect`: honour `num_simulations`.
- `too many digits`: round with `float(f"{p:.7e}")` last.
- `Start time timestamp is incorrect`: parse the prompt's `start_time`; never use the clock.

**`/validation/miner` says valid, but the prompt still scores as a miss (`crps = -1`).**
- Cause: a simulated price of exactly 0 (a `False` becomes 0 too), or a non-finite total.
- Fix: keep every price strictly positive, for example with multiplicative or log-return
  dynamics.

## Prices and market data

- **Every BTC, ETH, SOL and XRP request fails. The stock miner logs `Error in response of Binance
  API` and `Failed to fetch current price for asset: BTC`.**
  - Cause: `api.binance.com` returns HTTP 451 in some regions, such as the US.
  - Fix: set `BINANCE_API_HOST=https://data-api.binance.vision` in the miner's environment (PM2
    `env`, compose `environment:`, or the shell). It is read at import time, and `.env` files are
    not loaded.
- **Intermittent failures on Hyperliquid assets.**
  - Cause: the reference fetch reads the last 1-minute candle of the past 5 minutes, and an empty
    or failed answer raises.
  - Fix: cache the last good price from a background refresher.
- **Prompts for a new asset fail with `unsupported asset X`, and other assets time out too.**
  - Cause: the code predates the asset. On the stock miner the price fetch retries that error for
    15–30 s on the event loop, which blocks every other prompt meanwhile.
  - Fix: pull synth-subnet (the miner's asset maps mirror the validator's) and restart promptly.
    Watch the CHANGELOG.
- **Paths start at the wrong level for SP500 or another equity.**
  - Cause: the instrument scored is the Hyperliquid `xyz` perp, not the cash index or a stock
    exchange price.
  - Fix: anchor on the scored instrument (see [protocol.md](protocol.md)). Only the absolute
    final-price term depends on the level.

## Scores and rewards

- **Scores are fine, but the reward weight is tiny for days.**
  - Cause: the new-miner backfill. Everything in the window before the first score counts as a
    miss. The ramp-up takes about 5 days for `crypto-1h` and about 11 days for 24h.
  - Fix: wait. Do not re-register; that restarts the ramp-up.
- **No scores yet for 24h prompts.**
  - Cause: first scores take about 25–27 h (about 1–2 h for `crypto-1h`). A newly added 24h asset
    has no scores in its first day, and the monitoring API answers `"source": "wip"` with empty
    rows until it does.
  - Fix: wait.
- **Every `reward_weight` dropped by 25% on 2026-10-21.**
  - Cause: the competition coefficient changed from 1/3 to 1/4. Emission shares did not change.
- **Zero weight in one competition, normal weight elsewhere.**
  - Cause: that competition's prompts are not handled: the 1h shape, or the com-equ assets.
  - Fix: support every (asset, `time_length`) of the competitions you want paid in.
- **`crypto-1h` got worse after 2026-09-22, and `vol_crps` is large.**
  - Cause: the volatility term. Most often every path has the same volatility, so the fan of
    path volatilities is too narrow.
  - Fix: first compare your `vol_crps` with the field's on the same prompts; see "Diagnosing
    `vol_crps`" in [model-and-backtesting.md](model-and-backtesting.md).
- **Garbage answers rank below misses.** That is by design: raw CRPS is capped at 10× the median,
  while misses get the p95.
- **Rank jumps around between days with no change on your side.**
  - Cause: a dense field. Small CRPS gaps move many places.
  - Fix: judge on CRPS per asset over several days.
- **The backtester's estimated USD does not match the chain.**
  - Cause: synth-lib may pin an older synth-subnet: a different competition split, the burn, and
    Yuma consensus.
  - Fix: compare ranks and CRPS instead.

## synth-lib serving

- **`<ASSET> is still warming up` errors after every start.**
  - Cause: assets come online one at a time, after their warm-up and the first calendar refresh,
    which can take up to about 10 minutes.
  - Fix: keep `market_data/` persistent, pre-warm it, and avoid needless restarts.
- **`warm-up incomplete for BTC: 451 …`, then `no partitions for BTC` errors.**
  - Cause: Binance is geo-blocked and the store was never filled. Startup does not fail on it.
  - Fix: set `BINANCE_API_HOST` and restart.
- **`refresh failed for BTC: 451`, with answers that stay valid but go stale.**
  - Cause: a pre-warmed store has stopped updating.
  - Fix: set `BINANCE_API_HOST` and restart.
- **`no partitions for <ASSET>`, or an empty store although you pre-warmed.**
  - Cause: the entrypoint was launched from another directory, and `market_data/` resolves against
    the working directory.
  - Fix: launch from the synth-lib root, or `cd` there in the supervisor or unit.
- **The 7-day context is short or flat for HYPE and the com-equ assets.**
  - Cause: Hyperliquid keeps about 3.5 days of 1-minute candles.
  - Fix: let the miner run a few days, and never delete `market_data/`.
- **`ImportError` for synth_lib or a model dependency at launch.**
  - Cause: `uv run` used a project without them.
  - Fix: run from the synth-lib checkout, and add the model's dependencies there.
- **The contract gate fails with `ModuleNotFoundError` for a file beside `modeling.py`.**
  - Fix: run the gate with `PYTHONPATH=champions/my_model`.
- **Prompts on thin assets fail with `time out or internal server error`, and the log shows
  `ValueError#…: Out of range float values are not JSON compliant`.**
  - Cause: `simulate()` produced NaN from the `open`, `high`, `low` or `volume` columns, which are
    NaN on untraded minutes.
  - Fix: make `simulate()` NaN-safe on every column except `close`, and bound the paths.
- **`… predates the OHLCV columns`.**
  - Cause: partitions from an old store.
  - Fix: re-ingest those days with `market_data.py --force-refresh`.

## Docker, PM2 and provisioned VMs

- **`docker compose run --rm miner btcli …` starts the miner instead of btcli.**
  - Fix: add `--entrypoint btcli`.
- **A custom model is not in the image.**
  - Cause: the Dockerfile clones upstream at build time.
  - Fix: `COPY` your code or clone your fork, then build with `--no-cache`.
- **A ufw allow-list does not protect a Docker miner.**
  - Cause: published ports bypass ufw.
  - Fix: filter in the cloud firewall or the `DOCKER-USER` chain.
- **`chain_sync.py` keeps reporting a blanket rule, and `8091 ALLOW Anywhere` remains.**
  - Cause: the port was opened as `ufw allow 8091` (any protocol), as the tutorial and the
    Terraform script do, but `chain_sync.py` removes only `8091/tcp`.
  - Fix: `sudo ufw allow 8091/tcp && sudo ufw delete allow 8091`.
- **Locked out of SSH after `ufw enable`.**
  - Fix: run `ufw allow OpenSSH` before enabling ufw.
- **`sudo ufw enable` over SSH does nothing.**
  - Cause: it asks for confirmation.
  - Fix: use `sudo ufw --force enable`.
- **`npm install -g pm2` warns about engines on Ubuntu 22.04.**
  - Cause: apt's Node 12 is below PM2 7's Node ≥ 18.
  - Fix: install Node 22 from NodeSource, or `npm install -g pm2@5.4.3`.
- **`Permission denied` editing `/opt/synth-subnet`, or `uv: command not found`.**
  - Cause: Terraform and Ansible install as root, uv included.
  - Fix: `sudo chown -R <user>: /opt/synth-subnet`, install uv for your user, and keep your config
    in `miner.local.config.js`.
- **`git pull` aborts on `miner.config.js`, or conflicts on `synth/miner/simulations.py`.**
  - Cause: the provisioners overwrite the tracked config, and upstream changes `simulations.py`
    with every asset change.
  - Fix: run `git checkout -- miner.config.js` and keep your config in the gitignored local file.
    Keep your model in its own module ([protocol.md](protocol.md)).
- **A model dependency disappeared after `uv sync`.**
  - Cause: `uv sync` removes packages that are not in synth-subnet's lock.
  - Fix: keep them in your own project, or use `uv sync --inexact`.
- **The GCP VM misbehaves after a reboot.**
  - Cause: the startup script runs again on every boot, and it is not idempotent.
  - Fix: set up `pm2 startup` + `pm2 save`, and remove or guard the startup script.

## Public API

- **HTTP 429.**
  - Cause: the API is rate-limited (a couple of requests per second, per synth-lib's README).
  - Fix: slow down and back off before retrying.
- **HTTP 400 `date range cannot exceed …`.**
  - Cause: `to` covers the whole UTC day.
  - Fix: shorten the range, or page by day.
- **HTTP 404 on `/validation/scores/latest` for 1h.**
  - Cause: `time_increment=60` is missing (the default is 300).
- **HTTP 404 on `/validation/realized-path`.**
  - Cause: `start_time` must be an exact prompt start.
  - Fix: list the starts with `/validation/prompts`.
- **HTTP 500 on `/validation/miner`.**
  - Cause: usually the UID has no predictions at all.
- **`/v2/leaderboard/latest` returns `null`.**
  - Cause: the newest rewards round has not reached that competition yet.
  - Fix: retry later, or use the monitoring leaderboard.
- **Rows that predate your registration.**
  - Cause: the UID was recycled.
  - Fix: filter with `/miners/registrations/historical`.

# Deploying a miner

Contents:
- Done means
- A. Manual install under PM2
- B. Docker (`miner-setup/docker`)
- C. Cloud VM with Terraform, or Ansible on an existing VM
- D. Serving your own `simulate()` with synth-lib
- Network, firewall and validator allow-list
- Is it healthy? ("online" is not enough)
- Several miners, several UIDs
- Updating a miner

## Done means

Whichever path you take, check all three:
- **The port is reachable** from outside. The on-chain address shows in the AXON column of
  `btcli wallet overview`.
- **Requests arrive.**
  - The inherited blacklist logs `Requesting UID: <uid> | Stake at UID: <stake>` for each request
    from a registered validator, on every path.
  - The stock miner also logs `Received prediction request from <hotkey>`. A miner that overrides
    `forward_miner`, including synth-lib's, does not.
- **The validator accepts the answer.** `https://api.synthdata.co/validation/miner?uid=<UID>`
  returns `"validated": true`, with an empty `reason`. See
  [api-and-monitoring.md](api-and-monitoring.md).

## A. Manual install under PM2

This assumes the install in [setup.md](setup.md) is done. For a long-lived miner, keep your model in
your own module behind a `Miner` subclass rather than editing tracked files; see
[protocol.md](protocol.md), "Where to plug a model in".

1. **Keep your own PM2 config in a gitignored file.** `.gitignore` covers `miner.*local.config.js`,
   so a `git pull` will not fight your edits.

   ```bash
   cp miner.config.js miner.local.config.js
   ```

2. **Edit it.** An absolute interpreter and `cwd` make it independent of the shell PM2 is started
   from. Without them, `interpreter: "python3"` resolves to Ubuntu's system Python 3.10, and
   `PYTHONPATH: "."` points at whatever directory PM2 was started in.

   ```js
   module.exports = {
     apps: [
       {
         name: "miner",
         interpreter: "/home/ubuntu/synth-subnet/.venv/bin/python3",
         script: "./neurons/miner.py",
         cwd: "/home/ubuntu/synth-subnet",
         args: "--netuid 50 --subtensor.network finney --wallet.name miner --wallet.hotkey default --axon.port 8091 --blacklist.validator_min_stake 65000 --logging.debug",
         env: {
           PYTHONPATH: ".",
           // BINANCE_API_HOST: "https://data-api.binance.vision",  // hosts where api.binance.com returns 451
         },
       },
     ],
   };
   ```

   - Always pass `--netuid`. The code default is 1, not 50.
   - The shipped config also passes `--logging.trace`, which overrides `--logging.debug` and is
     very verbose. Use it only while debugging, with log rotation on.
3. **Start it, and make it survive reboots:**

   ```bash
   pm2 start miner.local.config.js
   pm2 startup                         # then run the sudo command it prints
   pm2 save                            # restore this process list on boot
   pm2 install pm2-logrotate           # PM2 does not rotate its logs by default
   ```

4. **Change it the right way:**

   ```bash
   pm2 restart miner                                  # after changing code
   pm2 restart miner.local.config.js --update-env     # after changing args or env in the config file
   pm2 save                                           # after any change, or a reboot brings back the old one
   pm2 describe miner                                 # shows the script args PM2 really passes
   ```

   - `pm2 start miner.config.js -- <flags>`, used in docs/miner_reference.md, does not work: PM2
     drops every argument after `--` when it starts from a config file.
   - `pm2 restart miner -- <flags>` replaces the whole argument list. The miner comes back with
     `--netuid 1` and the default wallet.
   - Change flags in the config file instead.
5. **To debug without PM2,** run the same command in the foreground:

   ```bash
   PYTHONPATH=. python neurons/miner.py --netuid 50 --wallet.name miner --wallet.hotkey default \
     --axon.port 8091 --blacklist.validator_min_stake 65000 --logging.debug
   ```

**Flag parsing.** bittensor parses flags leniently, so a misspelled or removed flag is silently
ignored.
- Compare the full config the miner logs at startup with your flags. For example,
  `validator_min_stake: 65000` despite `--blacklist.validator.min_stake 0` means that flag is
  misspelled.
- Do not use `--strict`: the miner parses its flags once without the miner-only options, so
  `--strict` rejects valid flags such as `--blacklist.validator_min_stake` and exits.
- An exported `BT_NO_PARSE_CLI_ARGS=true` makes bittensor skip every flag. The miner then dies at
  startup with `AttributeError: 'NoneType' object has no attribute 'name'`. Leave that variable
  unset.

**Startup log lines to expect:**
- `Subtensor: Network: finney, Chain: wss://…` and
  `Running neuron on subnet: 50 with uid <uid> using network: wss://…`. These two show the chain
  endpoint actually in use.
- `Serving miner axon …`. Its "on network:" part only echoes the configured value.
- `Miner starting at block: …`. If it never appears, the first sync failed before the axon started.
- every 20 s: `Miner | Step:… | UID:… | Stake:… | Incentive:… | Emission:…`.

## B. Docker (`miner-setup/docker`)

```bash
cd miner-setup/docker && cp .env.example .env && $EDITOR .env && docker compose build
docker compose run --rm --entrypoint btcli miner wallet regen-coldkeypub --wallet.name miner --ss58 <COLDKEY_SS58>
# the human seeds the hotkey file into the wallet-data volume, or runs regen-hotkey the same way
docker compose up -d && docker compose logs -f
```

Know these before relying on it:

- **btcli commands need `--entrypoint btcli`.** The README shows
  `docker compose run --rm miner btcli …`, but the image's `entrypoint.sh` ignores its arguments
  and starts the miner instead.
- **The image clones synth-subnet's default branch at build time,** using the old
  `mode-network/synth-subnet` URL, which redirects to `synthdataco`. Your local edits, including a
  custom model, are not in the image.
  - To ship your model, change the Dockerfile to `COPY` your code, or to clone your fork.
  - Then rebuild with `docker compose build --no-cache`; a plain rebuild reuses the cached clone.
- **Compose forwards only six variables** (`WALLET_NAME`, `WALLET_HOTKEY`, `NETWORK`, `NETUID`,
  `AXON_PORT`, `VALIDATOR_MIN_STAKE`). Add others, such as `BINANCE_API_HOST` or
  `BT_AXON_EXTERNAL_IP`, under `services.miner.environment`.
- **`VALIDATOR_MIN_STAKE=1000` in `.env.example` has no effect.** The code floors the value at
  65000.
- **Wallets live in the named volume `wallet-data`.** It is `docker_wallet-data` under the default
  project name. `docker compose down -v` deletes it, so never pass `-v`.
- **Docker-published ports bypass ufw.** A ufw allow-list, including `chain_sync.py`, does not
  protect a Docker miner. Restrict the port at the cloud firewall or in the `DOCKER-USER` iptables
  chain instead.
- **`restart: unless-stopped` restarts the container on any exit,** including a deregistered
  hotkey, which then loops; check `docker compose logs`.

## C. Cloud VM with Terraform, or Ansible on an existing VM

```bash
# Terraform. GCP needs project_id + ssh_public_key; AWS needs an existing key_pair_name.
cd miner-setup/terraform/gcp      # or aws
cp terraform.tfvars.example terraform.tfvars && $EDITOR terraform.tfvars
terraform init && terraform apply           # creates billable cloud resources: confirm with the user
terraform output ssh_command
# Watch the startup script until "Miner VM setup complete!"
#   GCP: sudo journalctl -u google-startup-scripts.service -f
#   AWS: sudo tail -f /var/log/cloud-init-output.log

# Ansible
cd miner-setup/ansible && cp inventory.example inventory && $EDITOR inventory
ansible-playbook -i inventory setup-miner.yml      # needs the community.general collection (ufw, npm modules)
```

Both paths install into `/opt/synth-subnet` as root, add a 12 GB swapfile, open SSH and the axon
port to `0.0.0.0/0`, and write a PM2 config with an absolute venv interpreter.

The provisioner then prints next steps that create the wallet and register on the VM. Ignore those
two. Instead:
1. Import only the hotkey file and `coldkeypub.txt` ([setup.md](setup.md)).
2. Have the human register from the machine that holds the coldkey.
3. Start PM2 from your own copy of the config:
   `cd /opt/synth-subnet && cp miner.config.js miner.local.config.js && pm2 start miner.local.config.js`.

Gaps to close by hand:
- **Reboots.** Neither path sets up `pm2 startup` or `pm2 save`. On GCP the startup script also
  runs again on every boot, and it is not idempotent.
- **Ownership and uv.** The repo and venv are owned by root, and uv is installed only for root.
  Run `sudo chown -R <user>: /opt/synth-subnet`, then install uv for your user:
  `curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env`.
- **Tracked config.** The provisioners overwrite the tracked `miner.config.js`. Keep your own
  config in `miner.local.config.js`.
- **Ansible re-runs.** The playbook clones with `force: true`, so running it again discards local
  edits and moves to the newest upstream commit.
- **IP changes.** Both create ephemeral public IPs. Reserve a static IP, or restart the miner after
  any IP change.
- **Firewall scope.** The axon is open to everyone.
  - The Terraform startup script opens it as `ufw allow 8091` (any protocol), and `chain_sync.py`
    can only remove a `8091/tcp` rule.
  - Before adding chain_sync, run `sudo ufw allow 8091/tcp && sudo ufw delete allow 8091`.
- **Ansible and the cloud firewall.** Ansible configures only the host firewall, so add the cloud
  ingress rule yourself.
- **AWS instance type.** The AWS default, `t3.xlarge`, is burstable. A CPU-heavy model can run out
  of credits; pick another `instance_type` if needed.

## D. Serving your own `simulate()` with synth-lib

synth-lib (public, Python ≥ 3.12) contains a miner shell, `synth_lib.serving.ChampionMiner`. It is
public code but not yet documented in synth-lib's README, so review it like your own code. It
subclasses synth-subnet's `Miner` and:
- keeps a minute OHLCV store for every competition asset, filled from the venue each asset is
  scored on (Binance spot, or Hyperliquid);
- warms that store in the background and refreshes it every minute;
- tops up `crypto-1h` contexts with the bars that closed just before the prompt;
- calls your `simulate()` with a 7-day context;
- converts the result to the live response contract (`wrap_output`).

The same `simulate()` runs in synth-lib's backtester, so you serve exactly what you measured
([model-and-backtesting.md](model-and-backtesting.md)).

**Prerequisites on the miner host:**
- uv: `curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env`;
- Node ≥ 18 and PM2, if PM2 supervises the miner ([setup.md](setup.md));
- the hotkey file and `coldkeypub.txt` ([setup.md](setup.md)).

btcli on this host is `uv run btcli`, run from the synth-lib root.

**1. Lay out the model inside a synth-lib checkout:**

```bash
git clone https://github.com/synthdataco/synth-lib.git && cd synth-lib && uv sync --extra benchmark
mkdir -p champions/my_model    # holds modeling.py, miner.py and entrypoint.sh
```

**2. Write `champions/my_model/modeling.py`.** It holds your model, behind this contract:

```python
def simulate(asset, start_time, time_increment, time_length, num_simulations, context_prices):
    """Return (start_time, time_increment, *paths): num_simulations paths, each with
    time_length // time_increment + 1 prices, starting at the last price of the context."""
```

- **`context_prices`** is a minute-indexed DataFrame of the 7 days before `start_time`, with
  columns `open, high, low, close, volume, trade_count`. It holds one asset only.
- **Missing values differ between serving and backtests.**
  - When serving, `close` is forward- and back-filled, while the other columns stay NaN on minutes
    without a trade.
  - In backtests, `close` itself can be NaN.
  - Treat a minute as a real print only when `close` and `volume` are both present, and compute
    returns only between adjacent real prints.
  - Filled minutes add zero returns; skipping NaNs folds several minutes into one "1-minute"
    return. Either way, volatility comes out biased. Never interpolate.
- **The index** is a tz-aware `DatetimeIndex` at microsecond resolution (pandas 3). It is unnamed
  when serving and named `timestamp` in backtests. Use datetime accessors, not raw integer
  nanoseconds or the index name.
- **Randomness.** Seed every generator from `SYNTH_BENCHMARK_SEED`, read inside `simulate()`.
- **Bound the paths yourself.** The shell has no guards:
  - an exception is a miss;
  - a NaN or inf price makes the axon answer HTTP 500, also a miss.
- **Data files.** Load calibration files relative to `__file__`, and check they are present
  before going live. A model missing its fitted data still passes every format check.

**3. Write `champions/my_model/miner.py`.** Do not override `forward_miner`. If you ever do, the
module that defines it must not use `from __future__ import annotations`: the axon reads the real
`Simulation` class from that annotation, and the miner will not start otherwise.

```python
"""Serves champions/my_model/modeling.py on Synth (SN50) through synth-lib's ChampionMiner."""

import logging
import time

from synth_lib.serving.champion_miner import ChampionMiner

# entrypoint.sh runs this file as a script, so this directory is first on sys.path.
from modeling import simulate


class MyModelMiner(ChampionMiner):
    simulate_fn = staticmethod(simulate)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with MyModelMiner() as miner:
        while True:
            miner.print_info()
            time.sleep(60)
```

**4. Write `champions/my_model/entrypoint.sh`** and make it executable with `chmod +x`:

```bash
#!/usr/bin/env bash
# Run from the synth-lib checkout root: uv finds the project there, and market_data/ resolves
# against the working directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NETWORK="${NETWORK:-finney}"
NETUID="${NETUID:-50}"
COLDKEY_NAME="${COLDKEY_NAME:-miner}"
HOTKEY_NAME="${HOTKEY_NAME:-default}"
PORT="${PORT:-8091}"
VALIDATOR_MIN_STAKE="${VALIDATOR_MIN_STAKE:-65000}"

export SYNTH_BENCHMARK_SEED="${SYNTH_BENCHMARK_SEED:-0}"
export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"

exec uv run python "${SCRIPT_DIR}/miner.py" \
    --subtensor.network "$NETWORK" \
    --netuid "$NETUID" \
    --logging.debug \
    --wallet.name "$COLDKEY_NAME" \
    --wallet.hotkey "$HOTKEY_NAME" \
    --axon.port "$PORT" \
    --blacklist.validator_min_stake "$VALIDATOR_MIN_STAKE"
```

**5. Check the model before going live,** from the synth-lib root. Add
`PYTHONPATH=champions/my_model` to either command if `modeling.py` imports a sibling module.

```bash
# Shape at 1000 paths, both formats (24h and 1h), on a synthetic context. Prints {"ok": ..., "msg": ...}
uv run python -m synth_lib.benchmark.nomination champions/my_model/modeling.py
# The live validator contract (int metadata, 8 significant digits) through wrap_output, both formats.
uv run python -c "from pathlib import Path; from synth_lib.serving.unpack_champion import contract_gate; contract_gate(Path('champions/my_model/modeling.py'), ('low', 'high')); print('contract gate OK')"
```

**6. Pre-warm the store, then run a real pre-flight** over every asset at 1000 paths. On a
geo-blocked host, export `BINANCE_API_HOST` first.

```bash
uv run synth_lib/preparation/market_data.py --days 8      # all assets
uv run python - <<'EOF'
import sys, time
from datetime import UTC, datetime, timedelta
sys.path.insert(0, "champions/my_model")
from modeling import simulate
from synth.simulation_input import SimulationInput
from synth.validator.competition_config import ALL_COMPETITIONS
from synth.validator.response_validation_v2 import validate_responses
from synth_lib.serving.serve import serve_request, venue_store

start = (datetime.now(UTC) + timedelta(minutes=2)).replace(second=0, microsecond=0)
for comp in ALL_COMPETITIONS:
    for asset in comp.asset_list:
        si = SimulationInput(asset=asset, start_time=start.isoformat(), time_increment=comp.time_increment,
                             time_length=comp.time_length, num_simulations=1000)
        t0 = time.perf_counter()
        out = serve_request(simulate, venue_store(asset), si)
        print(f"{comp.label:26} {asset:7} {time.perf_counter() - t0:6.2f}s  {validate_responses(out, si, '1.0')}")
EOF
```

- **Hyperliquid history is short.** It keeps only about 5000 one-minute candles (~3.5 days), so for
  HYPE and every com-equ asset, older days come back empty. Those assets start with a shorter
  context, which fills in as the miner runs.
- **Keep `market_data/` on persistent storage.** In a container, mount it as a volume.
- **Watch the timings.** Keep each call to a few seconds; several prompts share one minute.

**7. Launch** from the synth-lib root, under a supervisor that restarts on exit:

```bash
NETWORK=finney NETUID=50 COLDKEY_NAME=miner HOTKEY_NAME=default PORT=8091 ./champions/my_model/entrypoint.sh
# or, kept alive by PM2 (it runs .sh files with bash and keeps the env given at start):
NETWORK=finney NETUID=50 COLDKEY_NAME=miner HOTKEY_NAME=default PORT=8091 pm2 start champions/my_model/entrypoint.sh --name my-model
# on a geo-blocked host such as the US, prefix either command with:
#   BINANCE_API_HOST=https://data-api.binance.vision
```

Other environment:
- `BT_AXON_EXTERNAL_IP` / `BT_AXON_EXTERNAL_PORT` behind NAT or Docker port mapping.
- `FRED_API_KEY` if your model uses the event calendar. Read its path from
  `os.environ["SYNTH_BENCHMARK_CALENDAR"]` inside `simulate()`; the file is rewritten daily.
- `UV_NO_SYNC=1` in production, after a `uv sync --frozen`, so `uv run` does not re-sync on every
  start.

Behaviour to expect:
- **Warm-up after every start.** Assets come online one by one. Until an asset is ready, its prompts
  fail with `<ASSET> is still warming up`, and each one is a miss. This lasts up to about
  10 minutes, because the first event-calendar refresh is awaited. Avoid needless restarts.
- **A geo-blocked Binance is not fatal at startup, so it is easy to miss.**
  - `warm-up incomplete for BTC: 451 …` followed by `no partitions for BTC` errors means the store
    was never filled.
  - `refresh failed for BTC: 451` means a pre-warmed store has stopped updating, and answers go
    stale.
- **Every competition asset is served,** taken from the synth-subnet version synth-lib pins. To
  specialise:
  - raise in `simulate()` for the (asset, `time_length`) pairs of the competitions you drop. For
    example, `time_length == 86400 and asset in {"BTC", "ETH", "SOL", "XRP", "HYPE"}` drops
    `crypto-24h` and keeps `crypto-1h`;
  - dropping a whole competition costs only that competition;
  - dropping one asset of a competition you keep scores its prompts as misses there.
- **New assets.** When the subnet adds an asset, update synth-lib (its synth-subnet pin) and restart
  the miner.
- **`simulate()` runs serially on the axon's event loop.** Only the fresh-bar fetch is offloaded.
- **Use a restarting supervisor.** The pinned synth-subnet version may not retry the chain
  connection at startup.
- **Testnet.** Rehearse with `NETWORK=test NETUID=247`, keeping [setup.md](setup.md)'s caveats in
  mind. The installed synth-subnet is shared with mainnet, so never edit it in place. A
  testnet-only blacklist goes in a separate miner file that subclasses your miner.

## Network, firewall and validator allow-list

- **Open the port** in the host firewall. Allow SSH first, use `/tcp` so that `chain_sync.py` can
  later remove the blanket rule, and use `--force` so ufw does not prompt over SSH:

  ```bash
  sudo ufw allow OpenSSH && sudo ufw allow 8091/tcp && sudo ufw --force enable && sudo ufw status
  ```

  Then add a TCP 8091 ingress rule in the cloud firewall or security group.
- **Check it:**
  - locally: `nmap -p 8091 localhost` should show `8091/tcp open`;
  - from outside: an open-port checker, which reports "closed" once an allow-list is active;
  - on chain: the AXON column of `btcli wallet overview --wallet.name miner --wallet.hotkey default`,
    or this SDK one-liner, which also shows the registration:

    ```bash
    python -c "import bittensor as bt; m=bt.Subtensor('finney').metagraph(50); u=m.hotkeys.index('<HOTKEY_SS58>'); a=m.axons[u]; print(u, f'{a.ip}:{a.port}', a.is_serving)"
    ```

    `btcli subnets show` has no address column.
- **Behind NAT, a load balancer or Docker port remapping,** publish the public address:
  `--axon.external_ip <PUBLIC_IP> --axon.external_port <PUBLIC_PORT>`, or the env vars
  `BT_AXON_EXTERNAL_IP` / `BT_AXON_EXTERNAL_PORT`.
  - Without them, bittensor auto-detects the public IP and publishes `--axon.port`.
  - The axon always binds `0.0.0.0:<axon.port>` locally.
  - Validators send plain HTTP (no TLS) to `/Simulation` on the published port. A reverse proxy or
    load balancer in front must pass that through unchanged.
- **Who gets answered.** The built-in blacklist answers only requests that:
  - are signed;
  - come from a registered hotkey;
  - come from a validator that publishes an IP on chain;
  - come from a validator with stake strictly greater than `max(65000, --blacklist.validator_min_stake)`;
  - come from a validator with a validator permit.

  Keep this blacklist. It is also what stops strangers from harvesting your paths.
- **Network-level allow-list (optional).** `synth/miner/chain_sync.py` keeps one ufw rule per
  eligible validator IP, read from the metagraph every 300 s, and then removes the blanket
  `8091/tcp` rule.
  - It needs root and the venv's Python, and it loops forever, so run it under a supervisor.
  - It defaults to mainnet: on testnet pass `--network test --netuid 247`.

  ```bash
  python synth/miner/chain_sync.py --axon_port 8091 --dry-run          # print the allow-list only
  sudo /path/to/synth-subnet/.venv/bin/python synth/miner/chain_sync.py --axon_port 8091
  ```

  It derives the allow-list from the chain. Do not use hard-coded validator IPs from old notices.

## Is it healthy? ("online" is not enough)

PM2 "online" or Docker "Up" only means the Python process exists. Inside it, the chain sync runs in
a background thread and the axon in another, and either can die while the process lives on. Check:

- **The Step counter.** `Miner | Step:N` lines every 20 s must keep increasing.
  - A frozen `Step` means the sync thread died. The blacklist then works from a stale metagraph,
    and new validators get `Unrecognized hotkey`.
  - Look just before the freeze for `Unknown error` (an exception) or
    `is not registered on netuid` (deregistered; no traceback follows).
- **`Exception in thread … (run):`** in the error log, with no `Miner starting at block:` line,
  means the first sync failed before the axon started: nothing listens on the port.
- **A climbing restart count** (`pm2 list`) usually means an exit loop, such as an unregistered
  hotkey: the miner exits with status 0, and PM2 restarts clean exits too. Add
  `stop_exit_codes: [0]` to the PM2 app to stop that loop.
- **Requests getting through.** Count `Requesting UID:` lines per validator, and compare them with
  the eligible validators from `chain_sync.py --dry-run`.
- **The validator's view.** `/validation/miner` shows one validator's verdict on your latest prompt.

## Several miners, several UIDs

- **Separate everything.** Give each miner its own PM2 `name`, `--axon.port` (opened in both
  firewalls) and hotkey. The shipped configs all use the name `miner` and port 8091.
- **The same model on several UIDs.** Each UID gets the same scores (FAQ #8), and each costs a
  registration and its own ramp-up. Only different models tell you anything new.

## Updating a miner

1. **Read every new `CHANGELOG.md` entry**, not only those tagged "**Miner action required**".
   - The tag marks the blacklist change (v1.10.0) and the `crypto-1h` volatility term (v1.13.0).
   - Asset changes (SPYX → SP500 and the move off Pyth in v1.11.0; XRP, HYPE and WTIOIL in v1.8.0)
     and the float32 limit (v1.11.1) came without it.
   - Changes usually land on `main` a few days before they go live. Watch it, and the releases
     feed: https://github.com/synthdataco/synth-subnet/releases.atom.
2. **Stock miner:**

   ```bash
   git describe --tags                 # what you run now; the package version is always 1.0.0
   git pull --ff-only && uv sync --frozen
   PYTHONPATH=. python <skill dir>/scripts/check_responses.py && pm2 restart miner
   ```

   - `uv sync` removes packages that are not in synth-subnet's lock, including your model's own
     dependencies. Keep those in your own project, or use `uv sync --frozen --inexact`.
   - `git pull` conflicts on files you edited, such as `synth/miner/simulations.py`, which upstream
     changes with every asset change. Keep your model in its own module to avoid that.
   - On provisioned VMs, run `git checkout -- miner.config.js` before pulling, and keep your config
     in `miner.local.config.js`.
3. **Docker:** `docker compose build --no-cache && docker compose up -d`.
4. **synth-lib serving:** `git pull && uv sync --extra benchmark`, then restart the entrypoint.
5. **Restart deliberately.** Every restart costs a few seconds of downtime, and in the synth-lib
   shell, minutes of warm-up. Restart when needed, not by reflex.

# Setup: host, wallet, registration

Contents:
- Pick a deployment path
- Host and network
- Install synth-subnet
- Wallet: what the human does, what the agent may do
- Registration
- Immunity, pruning and the ramp-up
- Testnet rehearsal
- Public miner profile

## Pick a deployment path

| Path                                 | Use it when                                                                                      | Model plumbing                                                                                                                                            |
| ------------------------------------ | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Manual install + PM2 (the tutorial)  | You want full control on one VM                                                                  | Your own module plus a `Miner` subclass, or an edit of `generate_simulations`. The stock code fetches the spot price itself, and nothing else             |
| `miner-setup/docker`                 | You want a container and the stock model                                                         | The image clones upstream at build time. A custom model requires changing the Dockerfile                                                                  |
| `miner-setup/terraform` (AWS or GCP) | You want a fresh cloud VM with everything installed                                              | Same as manual, under `/opt/synth-subnet`                                                                                                                 |
| `miner-setup/ansible`                | You already have an Ubuntu VM                                                                    | Same as manual, under `/opt/synth-subnet`                                                                                                                 |
| synth-lib serving shell              | Your model needs recent market history, and you want to serve the same `simulate()` you backtest | synth-lib keeps a 7-day minute store from the scored venues and hands your model the context. It is public code, not yet documented in synth-lib's README |

All five end in the same miner process (`neurons/miner.py`, or a subclass of it), so the protocol,
firewall and troubleshooting notes apply to all of them. See
[deployment.md](deployment.md) for each path.

## Host and network

- **Hardware** (`min_compute.yml`):
  - CPU: 2 cores at 2.5 GHz minimum, 4 cores at 3.5 GHz recommended, x86_64.
  - Memory: 16 GB RAM and 12 GB swap.
  - Disk: SSD, 10 GB minimum and 25 GB recommended.
  - No GPU, unless your own model needs one.
  - OS: Ubuntu 22.04. The tutorial says "20.04+", and every automated path installs 22.04.
- **Inbound:** TCP on the axon port (8091 by default) must be open in the host firewall (ufw) and
  in the cloud provider's firewall or security group. Validators send plain HTTP to
  `http://<published ip>:<port>/Simulation`.
- **Outbound:**
  - the chain websocket: `wss://entrypoint-finney.opentensor.ai:443` on mainnet,
    `wss://test.finney.opentensor.ai:443` on testnet;
  - HTTPS to Binance (BTC, ETH, SOL, XRP) and Hyperliquid (HYPE and every com-equ asset), for
    prices;
  - with synth-lib serving, also HTTPS to `api.stlouisfed.org` (FRED, which needs
    `FRED_API_KEY`), `www.federalreserve.gov`, `www.eia.gov` and `api.nasdaq.com`, for its event
    calendar. Without them it still serves, but the calendar misses those events, and each start
    can wait up to 10 minutes for the first calendar refresh.
- **Geo-blocking.** `api.binance.com` answers HTTP 451 from some regions, such as the US. Set
  `BINANCE_API_HOST=https://data-api.binance.vision` in the miner's process environment. It is read
  at import time, and the miner does not load `.env` files. The Terraform defaults (EU regions)
  avoid the problem.
- **A stable public IP helps.** The miner publishes its IP on chain only at startup. After an IP
  change, restart the miner, then allow about 15 minutes for validators to pick up the new address.
- **Keep the clock in sync with NTP.** The axon rejects requests whose nonce looks too old.

## Install synth-subnet (manual path)

These commands run non-interactively, so they also work over `ssh host '…'`:

```bash
sudo apt-get update && sudo apt-get install -y git curl pkg-config
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
git clone https://github.com/synthdataco/synth-subnet.git && cd synth-subnet
uv sync && source .venv/bin/activate && export PYTHONPATH=.
python synth/miner/run.py      # prints start_time, then CORRECT
```

- **Python and tools.** uv provisions Python 3.11, which is what `.python-version` asks for. `btcli`
  comes with the venv (bittensor-cli 9.22.3, bittensor 10.4.1).
- **Rust.** The tutorial installs a Rust toolchain. On x86_64 Linux every locked dependency ships
  a prebuilt wheel, so Rust is only needed if something must build from source. If you install it
  from a script, use `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y`.
- **Node and PM2.** Current PM2 releases (7.x) require Node ≥ 18, and Ubuntu 22.04's apt Node.js is
  12.x. Install a supported Node, such as 22 LTS from NodeSource, then PM2:

  ```bash
  curl -fsSL https://deb.nodesource.com/setup_22.x -o nodesource_setup.sh
  sudo -E bash nodesource_setup.sh && sudo apt-get install -y nodejs
  sudo npm install -g pm2
  ```

  To keep the apt Node instead, pin `sudo npm install -g pm2@5.4.3`.
- **Running `run.py` from a geo-blocked host.** Use
  `BINANCE_API_HOST=https://data-api.binance.vision python synth/miner/run.py`.
- **What `run.py` covers.** It only checks BTC 24h with 100 paths. Use `scripts/check_responses.py`
  from this skill for every shape at 1000 paths.

## Wallet: what the human does, what the agent may do

- **Two keys.**
  - The coldkey holds funds, signs transfers and registration, and owns the UID. Its file is
    password-encrypted.
  - The hotkey is the miner's identity and signs its axon traffic. Its file,
    `hotkeys/<name>`, is plaintext JSON holding the mnemonic and private key.
- **What the miner process needs:** only the hotkey file and the public coldkey (`coldkeypub.txt`).
- **The human, on a trusted machine,** does the four steps that need the coldkey:
  1. Create the wallet. This prompts for a password and prints both mnemonics; back them up
     offline.
  2. Fund the coldkey.
  3. Register.
  4. Sign the public profile, if wanted.

  btcli 9.22.3 also refuses to register unless the private hotkey file exists on the registering
  machine, so create both keys there:

  ```bash
  uv tool install "bittensor-cli==9.23.2"   # on the trusted machine; see "Registration" for versions
  btcli wallet create --wallet.name miner --wallet.hotkey default
  ```

- **Move the hotkey to the miner host.**
  - Copy `~/.bittensor/wallets/miner/hotkeys/default` and `coldkeypub.txt` from the trusted
    machine, through a secrets manager or `scp`.
  - Alternatively, the agent writes the public file on the host, and the human restores the
    hotkey there from their own terminal, never through the agent session: the prompt echoes the
    mnemonic.

    ```bash
    btcli wallet regen-coldkeypub --wallet.name miner --ss58 <COLDKEY_SS58_ADDRESS>      # agent: public data only
    btcli wallet regen-hotkey --wallet.name miner --wallet.hotkey default                # human: prompts for the mnemonic
    ```

- **The agent may:**
  - check that the files exist without reading them:
    `ls -l ~/.bittensor/wallets/miner/coldkeypub.txt ~/.bittensor/wallets/miner/hotkeys/default`;
  - check that no coldkey is on the host: `test ! -e ~/.bittensor/wallets/miner/coldkey`;
  - restrict the hotkey file:
    `chmod 700 ~/.bittensor/wallets/miner/hotkeys && chmod 600 ~/.bittensor/wallets/miner/hotkeys/default`;
  - run `btcli wallet overview`, the SDK one-liners below, and PM2.
- **The agent never:**
  - runs `wallet create`, `new-coldkey`, `new-hotkey`, `regen-coldkey` or `regen-hotkey`;
  - passes `--mnemonic`, `--seed` or `--json`;
  - answers a password or mnemonic prompt;
  - cats, copies or uploads a key file.
- **Do not encrypt the hotkey.** The miner decrypts it at startup and PM2 has no terminal to type a
  password into.
- **Wallet paths.** Wallets live in `~/.bittensor/wallets/<name>/` of the user who runs the
  process. Run btcli, PM2 and the miner as the same user, or pass `--wallet.path`.
- **Docker.** The wallet sits in a named volume, and `docker compose down -v` deletes it.

## Registration

1. **Read the live price.** Registration recycles (burns) TAO, and the price moves.
   - On 2026-10-27 it was at SN50's floor (`min_burn`) of 0.8 τ.
   - Each registration multiplies it by `burn_increase_mult`, then it decays back toward the floor
     with a half-life of `burn_half_life` blocks, up to a cap of `max_burn`. On 2026-10-27 these
     were 1.26, 360 blocks (about 72 minutes) and 100 τ. `btcli subnets hyperparameters --netuid 50`
     lists them (bittensor-cli 9.23.2 or later).
   - The docs' "0.25 TAO should be enough" is out of date.

   The bittensor SDK in the synth-subnet venv reads the price reliably:

   ```bash
   python -c "import bittensor as bt; print(bt.Subtensor('finney').recycle(50))"
   ```

   - `btcli subnets show --netuid 50` prints "Registration cost (recycled)", but with the pinned
     btcli 9.22.3 it can fail with `Storage function … not found`. A newer bittensor-cli works
     (9.23.2 did on 2026-10-27).
   - `btcli subnets burn-cost` is the cost of creating a new *subnet*. It is not this.
2. **Confirm with the user.** Show them the price and get an explicit yes. The TAO is not refunded.
3. **The human registers** from the trusted machine:

   ```bash
   btcli subnet register --wallet.name miner --wallet.hotkey default --netuid 50
   ```

   - btcli shows the cost and asks to confirm.
   - Safe registration (on by default, 5% tolerance) fails with `RegistrationPriceLimitExceeded`
     if the price jumps meanwhile; wait for it to decay and retry.
   - `Insufficient balance … Current recycle is … TAO` means the coldkey cannot cover price plus
     fee.
   - `HotKeyAlreadyRegisteredInSubNet` means that hotkey already holds a UID.
   - If this btcli fails with `Storage function … not found`, use a newer bittensor-cli.
4. **Verify:** `btcli wallet overview --wallet.name miner --wallet.hotkey default` lists the UID and,
   once the miner runs, its published address in the AXON column.

## Immunity, pruning and the ramp-up

SN50 is full (256 UIDs), so every registration evicts someone. The rules:
- **Who is evicted.** The UID with the lowest emission among those past their immunity period;
  ties go to the oldest registration.
- **Immunity period.** On 2026-10-27 it was 65535 blocks, about 9.1 days at 12 s per block. It is
  owner-settable, so read it live:

  ```bash
  python -c "import bittensor as bt; print(bt.Subtensor('finney').immunity_period(50))"
  ```

- **The ramp-up outlasts immunity.** A new hotkey is backfilled with miss-level scores for its
  window before it joined. `crypto-1h` clears that in about 5 days, the 24h competitions only in
  about 11 days.
  - With the immunity of 2026-10-27, days 9 to 11 are when newcomers get pushed out.
  - To survive, the model must out-earn the weakest unprotected UIDs while still carrying that
    backfill.
- **Before buying a UID:**
  - have the miner tested and ready, so it answers from the first block;
  - preview the ramp-up with synth-lib's `--simulate-registration`
    ([model-and-backtesting.md](model-and-backtesting.md));
  - avoid re-registering, which buys a new identity and starts the ramp-up again.
- **Immunity left for a hotkey:**

  ```bash
  python -c "import bittensor as bt; s=bt.Subtensor('finney'); mg=s.metagraph(50, lite=True); uid=s.get_uid_for_hotkey_on_subnet('<HOTKEY_SS58>', 50); left=s.immunity_period(50)-(s.get_current_block()-int(mg.block_at_registration[uid])); print(uid, left, 'blocks ~', round(max(left, 0)*12/3600, 1), 'h')"
  ```

## Testnet rehearsal

Testnet is netuid 247. It rehearses registration, ports and startup; it does not guarantee
validator traffic.

- **Register:**

  ```bash
  btcli subnet register --wallet.name miner --wallet.hotkey default --network test --netuid 247
  ```

  On 2026-10-27 the burn on 247 was at its 0.0005 τ floor.
- **Test TAO** comes from the community Discord, linked from the synth-subnet README. btcli 9.22.3
  has no faucet command. Check balances with `btcli wallet balance --wallet.name miner --network test`.
- **Run the miner** with `--netuid 247 --subtensor.network test`. Add `--network test` to btcli
  commands such as `wallet overview`.
- **The blacklist's 65000 minimum stake applies on testnet too.**
  - `miner.test.config.js` passes `--blacklist.validator.min_stake 0`: a misspelled flag, silently
    ignored, and the code floors the value at 65000 anyway.
  - Few testnet validators may qualify, and none is guaranteed to be sending prompts.
  - See who qualifies with
    `python synth/miner/chain_sync.py --axon_port 8091 --dry-run --network test --netuid 247`
    (chain_sync takes `--network`, not `--subtensor.network`).
  - For a testnet-only rehearsal with lower-stake validators, run a testnet-only miner file whose
    class overrides `blacklist` without the floor. Keep the exact signature
    `async def blacklist(self, synapse: Simulation) -> typing.Tuple[bool, str]`, in a module
    without `from __future__ import annotations`. Never launch that file on mainnet.
- **Monitoring.** The public API and dashboards report mainnet only. On testnet, confirm traffic
  from the miner log: `Requesting UID:` lines, plus `Received prediction request from` on the stock
  miner.
- **Rate limits.** The public testnet endpoint is rate-limited. Back off on HTTP 429, or run your
  own subtensor node.

## Public miner profile (optional)

Synth shows an optional display name, avatar and social links per coldkey on its dashboard. The
human runs this on the machine that holds the coldkey; it prompts for the coldkey password and signs
the profile:

```bash
python scripts/submit_miner_profile.py --wallet_name miner --display-name "My Mining Co" \
  --avatar-url https://example.com/logo.png --twitter my_handle --discord my_handle \
  --linkedin https://www.linkedin.com/in/my-handle --website https://example.com
python scripts/submit_miner_profile.py --wallet_name miner --delete    # clear it
```

- Each submission replaces all six fields.
- The coldkey must be (or have been) registered on the subnet.
- The signed message expires after 5 minutes.
- The `WALLET_PASS` variable mentioned in the script's comments has no effect with the pinned
  wallet library, so expect the password prompt.

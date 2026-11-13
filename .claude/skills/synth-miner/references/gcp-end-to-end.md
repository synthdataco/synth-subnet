# One go: from a funded wallet to a serving miner on GCP

Contents:
- Where it runs
- What the run does
- Inputs and conventions
- 1. Preflight
- When the wallet is not ready
- 2. Pick the model
- 3. Create the VM
- 4. Wait for provisioning
- 5. Copy the keys, then rehearse before registering
- 6. Register
- 7. Start and verify
- 8. Wait for the dashboard
- Later changes: FRED key, another model, tear-down

This runbook takes a laptop with an authenticated gcloud and a funded Bittensor wallet to a
registered SN50 miner on a GCP VM that serves a published autoquant champion, and follows it until
the UID shows on the dashboard.

## Where it runs

In a Claude Code session on the user's own machine: the `claude` CLI, or a local session in the
Code tab of the Claude Desktop app. Every command reads the laptop's gcloud credentials, wallet and
SSH key. Claude Desktop chat, Cowork and cloud sessions cannot see those files, so stop there and
tell the user to open a local Code session on a clone of synth-subnet, which loads this skill:

```bash
git clone https://github.com/synthdataco/synth-subnet.git ~/synth-subnet
```

**Consent.** Asking for this path with a funded wallet and the password file in place is the
user's consent to install uv and Terraform in `~/.synth-miner/bin` when they are missing, to create
the VM below, and to register within the ceiling in step 6. When both are ready, nothing in the run
asks the user anything. Claude Code's own permission prompts still apply.

## What the run does

1. Checks the laptop, the GCP project and the wallet, and installs uv and Terraform if missing.
2. Picks the model: the best eligible autoquant champion, the user's choice, or the stock miner.
3. Creates the VM with `miner-setup/terraform/gcp`, and waits until it is provisioned.
4. Copies `coldkeypub.txt`, the hotkey file and the optional FRED key file to the VM, then reboots
   it to prove the miner starts, loads its model and reaches the chain, all before any TAO is
   spent.
5. Registers the hotkey with `scripts/register.py`.
6. Starts the miner at once, and checks it from the VM, the laptop, the chain and the public API.
7. Waits in the background until the UID is on the `crypto-1h` leaderboard.

The order matters. The miner exits when its hotkey has no UID, and immunity starts at
registration, so the VM must be proven before TAO is spent, and the miner must start right after.

## Inputs and conventions

| Input          | Default                                            | Used by                                               |
| -------------- | -------------------------------------------------- | ----------------------------------------------------- |
| Wallet, hotkey | `miner`, `default`                                 | `register.py`, Terraform `wallet_name`, `hotkey_name` |
| Password file  | `~/.synth-miner/coldkey-password`, mode 0600       | `register.py`                                         |
| FRED key file  | `~/.synth-miner/fred_api_key`, mode 0600, optional | copied to the VM                                      |
| GCP project    | `gcloud config get-value project`                  | Terraform `project_id`                                |
| Zone, machine  | `europe-west1-b`, `e2-standard-4` with a 30 GB SSD | Terraform defaults                                    |
| Model          | the pick of `scripts/pick_champion.py`             | Terraform `champion`, `autoquant_ref`                 |
| Price ceiling  | the user's, or else the live price plus 5%         | `register.py --max-tao`                               |
| SSH key        | `~/.ssh/synth-miner_ed25519`, created if missing   | Terraform `ssh_public_key`, `ssh`, `scp`              |

- `<SKILL>` is this skill's base directory, shown when the skill loads. `<REPO>` is the
  synth-subnet clone chosen in the preflight. Write both as absolute paths.
- `uv` and `terraform` stand for `~/.synth-miner/bin/uv` and `~/.synth-miner/bin/terraform` when the
  preflight installed them there. Nothing is added to the user's shell profile.
- The commands use the defaults; substitute the user's values. Shell variables do not carry over
  from one command to the next, so write literal values (the IP, the zone) into each command, and
  run each code block as one command.
- `SSH_OPTS` below stands for `-i ~/.ssh/synth-miner_ed25519 -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new`. Write it out in full in each `ssh` and `scp` command.
  `BatchMode` makes a missing key fail instead of prompting.
- Give a 10-minute timeout to every command that downloads or waits: the installs, the first
  `uv run --script` of each script, `terraform init` and `apply`, and the polling loops. A loop
  that runs out of time is run again.

## 1. Preflight

Read-only, apart from installing missing tools, enabling the Compute API and creating the SSH key.
Print every result.

**Tools and account:**

```bash
for t in gcloud ssh scp curl unzip nc shasum; do command -v "$t" >/dev/null || echo "missing: $t"; done
git --version || echo "missing: git"
python3 --version || echo "missing: python3"
command -v uv || ls ~/.synth-miner/bin/uv
command -v terraform || ls ~/.synth-miner/bin/terraform
gcloud auth list --filter=status:ACTIVE --format='value(account)'
gcloud config get-value project
```

Install uv if it is missing:

```bash
mkdir -p ~/.synth-miner/bin
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.synth-miner/bin" UV_NO_MODIFY_PATH=1 sh
```

Install Terraform if it is missing. The zip is checked against HashiCorp's published SHA-256 before
it is unpacked:

```bash
V=1.17.2; P=$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/'); D=$(mktemp -d)
curl -fsSLo "$D/terraform_${V}_$P.zip" "https://releases.hashicorp.com/terraform/$V/terraform_${V}_$P.zip"
curl -fsSL "https://releases.hashicorp.com/terraform/$V/terraform_${V}_SHA256SUMS" | grep " terraform_${V}_$P.zip\$" > "$D/SHA256SUMS"
(cd "$D" && shasum -a 256 -c SHA256SUMS) && mkdir -p ~/.synth-miner/bin && unzip -o -q "$D/terraform_${V}_$P.zip" terraform -d ~/.synth-miner/bin
~/.synth-miner/bin/terraform version
```

**The clone.** `<REPO>` is the session's folder when it holds `miner-setup/terraform/gcp`.
Otherwise it is `~/synth-subnet`, cloned if missing:
`test -d ~/synth-subnet || git clone https://github.com/synthdataco/synth-subnet.git ~/synth-subnet`.
Then check that it carries this runbook's Terraform module; this must print `1`:

```bash
grep -c 'variable "champion"' <REPO>/miner-setup/terraform/gcp/variables.tf
```

**GCP project:**

```bash
gcloud services enable compute.googleapis.com --project <PROJECT>
gcloud compute networks describe default --project <PROJECT> --format='value(name)'
```

**Wallet:**

```bash
ls -l ~/.bittensor/wallets/miner/coldkeypub.txt ~/.bittensor/wallets/miner/hotkeys/default ~/.bittensor/wallets/miner/coldkey
ls -l ~/.synth-miner/coldkey-password
ls -l ~/.synth-miner/fred_api_key || echo "no FRED key file"
uv run --script <SKILL>/scripts/register.py --wallet-name miner --hotkey-name default --check
```

- **`register.py --check`** prints one JSON object: the coldkey and hotkey addresses, `registered`,
  `uid`, the live `price_tao` and `balance_tao`, and `axon` once registered. It needs no password.
  Its header pins bittensor, which uv installs in its cache on the first run.
- **Terraform credentials.** The Google provider ignores `gcloud auth login`. Prefix every
  `terraform` command that talks to GCP with
  `GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"`. The token lasts about an hour,
  so fetch a fresh one each time, and never print it. With Application Default Credentials set up
  (`gcloud auth application-default login`), the prefix is optional.
- **SSH key.** Create a dedicated key if there is none. Only the `.pub` half is ever read:

  ```bash
  test -f ~/.ssh/synth-miner_ed25519 || ssh-keygen -t ed25519 -N '' -C synth-miner -q -f ~/.ssh/synth-miner_ed25519
  cat ~/.ssh/synth-miner_ed25519.pub
  ```

What to do when a check fails:

| Result                                                    | Next                                                                                                                                             |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `missing: gcloud`, or another tool the user has installed | Desktop app: the user quits and reopens it to reload `PATH`. Else the user installs it (macOS: `xcode-select --install` for `git` and `python3`) |
| No active account                                         | The user runs `gcloud auth login` in their own terminal                                                                                          |
| No project set                                            | Ask the user which project to use                                                                                                                |
| The `grep` prints `0`, or the file is missing             | An older checkout. On a clean `main`, `git -C <REPO> pull --ff-only`; otherwise stop and tell the user                                           |
| `services enable` fails on billing                        | Stop: the user links a billing account to the project                                                                                            |
| No `default` network                                      | Stop: the Terraform module attaches to `default`. The user creates it or picks another project                                                   |
| Wallet files or password file missing                     | "When the wallet is not ready" below                                                                                                             |
| Password file not `-rw-------`                            | `chmod 600 ~/.synth-miner/coldkey-password`                                                                                                      |
| `registered: true`                                        | Skip the rehearsal in step 5, and step 6. The password file is not needed                                                                        |
| Exit 3 with `is encrypted`                                | Stop: the miner cannot type a password. The user makes an unencrypted hotkey ([setup.md](setup.md))                                              |
| Exit 4: the hotkey belongs to another coldkey             | Stop: wrong hotkey                                                                                                                               |
| `balance_tao` below the ceiling plus the fee              | "When the wallet is not ready", step 3                                                                                                           |

## When the wallet is not ready

The human does these in their own terminal, never through the agent's shell or Claude Code's `!`
prefix: both put the command and its output into the conversation, including a printed mnemonic.
Give them the exact commands, wait until they say they are done, then re-run the preflight. After
that, the run needs nothing more from them.

1. **No wallet.** With the synth-subnet venv (write `~/.synth-miner/bin/uv` for `uv` if the
   preflight installed it there):

   ```bash
   cd <REPO> && uv sync --frozen && source .venv/bin/activate
   btcli wallet create --wallet.name miner --wallet.hotkey default
   ```

   It asks for a new coldkey password and prints two mnemonics; write both down offline. It writes
   the hotkey unencrypted, which the miner needs. A wallet that has only a coldkey gets a hotkey
   with `btcli wallet new-hotkey --wallet.name miner --wallet.hotkey default`.
2. **No password file.** The password is typed without echo and never appears on a command line,
   so it stays out of the shell history:

   ```bash
   mkdir -p ~/.synth-miner && chmod 700 ~/.synth-miner
   echo "Coldkey password, then Enter:"; IFS= read -rs PW
   ( umask 077; printf '%s\n' "$PW" > ~/.synth-miner/coldkey-password ); unset PW
   ls -l ~/.synth-miner/coldkey-password
   ```

3. **Not funded.** Show the coldkey address from the `--check` output and the amount to send: the
   ceiling from step 6 plus a small margin for the fee. The user sends TAO there from an exchange
   or another wallet. Re-run `--check` until `balance_tao` covers it.
4. **FRED key (optional, never blocks the run).** Invite the user once. Champions that read
   synth-lib's event calendar use a free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html)
   for US macro releases (CPI, payrolls, GDP and others), and serve without them otherwise. Never ask
   for the key in the conversation. The user saves it the same way:

   ```bash
   echo "FRED API key, then Enter:"; IFS= read -rs K
   ( umask 077; printf '%s\n' "$K" > ~/.synth-miner/fred_api_key ); unset K
   ```

   Without the file, carry on. It can be added later.

## 2. Pick the model

```bash
uv run --script <SKILL>/scripts/pick_champion.py --synth-subnet <REPO>
```

- It prints one JSON object: `campaign`, `leg`, `score`, per-competition `reward_vs_top` and ranks,
  `window`, `data_cutoff`, `warnings`, and `terraform.champion` and `terraform.autoquant_ref`, the
  two Terraform values. Show the user the pick, its score and window, and every warning.
- The first run makes a small partial clone of autoquant in `~/.cache/synth-miner/autoquant`. It
  never downloads LFS files.
- **The user named a champion:** add `--campaign <campaign> --leg <leg>`. It then validates that leg
  instead, and exits 1 with the reasons when it is not eligible. Add `--force` only if the user
  insists after seeing them.
- **The user asked for the stock miner:** skip the picker and leave `champion` empty. Tell them it is
  a placeholder that ranks low ([protocol.md](protocol.md)).
- **Exit 1, nothing eligible:** stop and report. Do not register a stock miner unless the user asks
  for one.
- What the verdicts mean and why only the newest campaign counts: "Published champions" in
  [model-and-backtesting.md](model-and-backtesting.md).

## 3. Create the VM

Write `<REPO>/miner-setup/terraform/gcp/terraform.tfvars`. It is gitignored, like the state file:

```hcl
project_id     = "<PROJECT>"
ssh_public_key = "<the one line of ~/.ssh/synth-miner_ed25519.pub>"
wallet_name    = "miner"
hotkey_name    = "default"
champion       = "<terraform.champion>"        # "" for the stock miner
autoquant_ref  = "<terraform.autoquant_ref>"   # the full sha the picker read
```

- The other variables keep their defaults: `name = "synth-miner"`, zone `europe-west1-b`,
  `e2-standard-4`, a 30 GB SSD, SSH user `miner`, finney, netuid 50, port 8091.
- In a US region, add `binance_api_host = "https://data-api.binance.vision"` ([setup.md](setup.md)).
- To limit SSH to the laptop, add `ssh_source_ranges = ["<public IP>/32"]`. It only narrows SSH when
  the project has no `default-allow-ssh` rule, because GCP allow rules add up
  (`gcloud compute firewall-rules list --filter=network=default`).

Before applying, say in the transcript what it creates: the VM `synth-miner` in `europe-west1-b`, a
static IP, and firewall rules for TCP 8091 from anywhere and for SSH. They are billed while they
exist.

```bash
GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" terraform -chdir=<REPO>/miner-setup/terraform/gcp init -input=false
GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" terraform -chdir=<REPO>/miner-setup/terraform/gcp apply -input=false -auto-approve
terraform -chdir=<REPO>/miner-setup/terraform/gcp output
```

- The outputs are `miner_ip` (static), `instance_name`, `zone`, `ssh_user`, `ssh_command`,
  `wallet_dir` and `serve_mode`.
- `terraform.tfstate` stays in that directory, and is how the VM is changed or destroyed later.
  One checkout manages one miner. For a second, use `terraform workspace new <name>` and another
  `name`.
- An apply that fails on `constraints/compute.vmExternalIpAccess` means an organization policy
  forbids external IPs in that project. Use another project.

## 4. Wait for provisioning

The startup script prints `synth-miner: provisioned` when it is done, or
`synth-miner: FAILED at <step>`. Poll the serial port with a loop that ends:

```bash
for i in $(seq 1 18); do
  r=$(gcloud compute instances get-serial-port-output synth-miner --zone europe-west1-b 2>/dev/null \
      | grep -oE 'synth-miner: (provisioned|FAILED at [a-z-]+)' | tail -n 1)
  if [ -n "$r" ]; then echo "$r"; break; fi
  sleep 30
done
[ -n "$r" ] || echo "still provisioning"
```

- `synth-miner` and `europe-west1-b` are the `instance_name` and `zone` outputs.
- Each run waits up to 9 minutes. Run it again while it prints `still provisioning`. A champion
  takes longer than the stock miner: autoquant clone, LFS files, a Python 3.12 environment.
- If the shell refuses a foreground `sleep`, run the loop in the background and wait for it to
  exit.
- The last step reached: the same command with `grep -oE 'synth-miner: step [a-z-]+' | tail -n 1`.
- **No answer after 30 minutes, or no serial output at all** (an organization policy can disable
  it): read the markers over SSH. The guest agent installs the SSH key whatever the startup script
  does:

  ```bash
  ssh SSH_OPTS miner@<IP> 'if test -f /var/lib/synth-miner/provisioned; then echo provisioned; elif test -f /var/lib/synth-miner/failed; then echo "FAILED at $(cat /var/lib/synth-miner/failed)"; else echo running; fi'
  ```

- On `FAILED at <step>`, read [troubleshooting.md](troubleshooting.md), "Terraform VM (systemd)",
  fix the cause, then rerun the script on the VM with `sudo google_metadata_script_runner startup`.
  Every step can run again. The rerun prints its own result; the serial log still holds the old
  `FAILED` line.

## 5. Copy the keys, then rehearse before registering

Copy file to file with `scp`. Never `cat`, `head` or pipe a key file, and never copy `coldkey`:
it stays on the laptop. `<IP>` is `miner_ip` and `<WALLET_DIR>` is `wallet_dir`
(`/home/miner/.bittensor/wallets/miner`).

```bash
scp SSH_OPTS ~/.bittensor/wallets/miner/coldkeypub.txt miner@<IP>:<WALLET_DIR>/coldkeypub.txt
scp SSH_OPTS ~/.bittensor/wallets/miner/hotkeys/default miner@<IP>:<WALLET_DIR>/hotkeys/default
scp SSH_OPTS ~/.synth-miner/fred_api_key miner@<IP>:.config/synth-miner/fred_api_key   # only if the file exists
ssh SSH_OPTS miner@<IP> 'chmod 600 <WALLET_DIR>/coldkeypub.txt <WALLET_DIR>/hotkeys/default; test ! -f .config/synth-miner/fred_api_key || chmod 600 .config/synth-miner/fred_api_key; test ! -e <WALLET_DIR>/coldkey && ls -l <WALLET_DIR> <WALLET_DIR>/hotkeys .config/synth-miner'
```

Then check the model on the VM:

- **Champion.** The `unpack` step already ran synth-lib's contract gate; provisioning fails
  otherwise. Print the provenance, and whether the champion reads the event calendar:

  ```bash
  ssh SSH_OPTS miner@<IP> 'head -n 6 /opt/autoquant/champions/champion/PROVENANCE.md; grep -rlq SYNTH_BENCHMARK_CALENDAR /opt/autoquant/champions/champion --include="*.py" && echo "reads the event calendar" || echo "no event calendar"'
  ```

  If it reads the calendar and no FRED key was copied, tell the user once (step 4 of "When the
  wallet is not ready"), and carry on.
- **Stock miner.** Run the response check on the VM. It must exit 0; otherwise stop before
  registering and fix the model ([protocol.md](protocol.md)):

  ```bash
  scp SSH_OPTS <SKILL>/scripts/check_responses.py miner@<IP>:/tmp/check_responses.py
  ssh SSH_OPTS miner@<IP> 'set -a; . /etc/synth-miner/miner.env; set +a; cd /opt/synth-subnet && PYTHONPATH=. .venv/bin/python /tmp/check_responses.py'
  ```

**Rehearse with a reboot.** Skip it when the hotkey is already registered. Now that the hotkey is in
place, the unit starts at boot, loads the model, the wallet and the metagraph, and then exits at its
registration check with `is not registered on netuid 50`. That line in the new boot proves the boot
start and the whole start path, before any TAO is spent:

```bash
ssh SSH_OPTS miner@<IP> 'cat /proc/sys/kernel/random/boot_id'
ssh SSH_OPTS miner@<IP> 'sudo systemctl reboot' || true
for i in $(seq 1 30); do sleep 15; ssh SSH_OPTS -o ConnectTimeout=5 miner@<IP> 'grep -vqx <OLD_BOOT_ID> /proc/sys/kernel/random/boot_id && sudo journalctl -u synth-miner -b --no-pager | grep -q "is not registered on netuid"' 2>/dev/null && break; done
ssh SSH_OPTS miner@<IP> 'sudo systemctl stop synth-miner; sudo journalctl -b --no-pager | grep -o "synth-miner: already provisioned"; sudo journalctl -u synth-miner -b --no-pager | grep -o "is not registered on netuid [0-9]*" | head -n 1'
```

- `<OLD_BOOT_ID>` is the first command's output. The reboot command can report a closed
  connection.
- Expect both lines. The unit restarts every 15 s until it is stopped, which is why the last
  command stops it.
- If the second line is missing, read `sudo journalctl -u synth-miner -b --no-pager | tail -n 60`
  on the VM and [troubleshooting.md](troubleshooting.md), and do not register until it shows.

## 6. Register

Read the live price just before registering:

```bash
uv run --script <SKILL>/scripts/register.py --wallet-name miner --hotkey-name default --check
```

The ceiling, in τ, from this read:
- the user's ceiling, if they gave one;
- otherwise `price_tao` plus 5%;
- if `price_tao` is above 2 τ and the user gave no ceiling, stop and ask.

```bash
uv run --script <SKILL>/scripts/register.py --wallet-name miner --hotkey-name default --max-tao <CEILING>
```

- It reads the password from `~/.synth-miner/coldkey-password` itself, never prompts, and prints one
  JSON object of public data on stdout. Show it, and keep `hotkey` and `uid` for step 7.
- The chain refuses the registration if the price rises above `--max-tao` before inclusion.
- A hotkey that already holds a UID is reported as registered, without reading the password, so a
  re-run never registers twice.

| Exit | Meaning                                      | Next                                                                                                                 |
| ---- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 0    | Registered, or already registered            | Step 7 at once                                                                                                       |
| 3    | Wallet file missing, or the hotkey encrypted | Fix the files; see the preflight table                                                                               |
| 4    | The hotkey belongs to another coldkey        | Stop: wrong hotkey                                                                                                   |
| 5    | Live price above the ceiling                 | Wait for it to decay; see below the table                                                                            |
| 6    | Balance below price plus fee                 | The user funds the coldkey ("When the wallet is not ready", step 3)                                                  |
| 7    | Password file missing, empty or too open     | The user rewrites it ("When the wallet is not ready", step 2)                                                        |
| 8    | Wrong password                               | Same                                                                                                                 |
| 9    | The chain rejected it (`error` names why)    | `RegistrationPriceLimitExceeded`: as exit 5. `TooManyRegistrationsThisInterval`: retry in a few minutes. Else report |
| 10   | Included, but no UID after 5 blocks          | Run `--check` before anything else. Never submit again blindly                                                       |
| 1, 2 | Unexpected error, or usage error             | Read stderr                                                                                                          |

- **Exit 5.** Another registration raised the price, and it decays back within the hour. Re-run
  `--check` every 10 minutes, and register with the same ceiling once `price_tao` is below it. After
  an hour, ask the user. Never raise the ceiling yourself.

## 7. Start and verify

Start the miner as soon as registration succeeds, and wait for its first chain sync:

```bash
ssh SSH_OPTS miner@<IP> 'sudo systemctl start synth-miner; for i in $(seq 1 30); do sudo journalctl -u synth-miner -b --no-pager | grep -q "Miner starting at block" && break; sleep 10; done; systemctl is-active synth-miner; sudo journalctl -u synth-miner -b --no-pager | grep -E "Running neuron on subnet|Serving miner axon|Miner starting at block" | tail -n 3'
```

Then print each piece of evidence:

1. **Service.** `active`, and a `Miner starting at block:` line.
2. **Port, from the laptop:** `nc -z -w 5 <IP> 8091 && echo "8091 open"`.
3. **On-chain address equals the static IP:** `register.py --check` (step 6) now shows
   `"axon": "<IP>:8091"`. If it shows `null`, run it again a minute later.
4. **Champion warm-up.** Prompts fail with `still warming up` until `warm-up complete;`, up to
   about 10 minutes after each start. The per-asset `warm-up complete for …` lines come earlier
   and do not count:

   ```bash
   ssh SSH_OPTS miner@<IP> 'for i in $(seq 1 54); do sudo journalctl -u synth-miner -b --no-pager | grep -q "warm-up complete;" && break; sleep 10; done; sudo journalctl -u synth-miner -b --no-pager | grep -E "warm-up complete;|warm-up incomplete" | tail -n 5'
   ```

   A `warm-up incomplete for <asset>` line: see [troubleshooting.md](troubleshooting.md).
5. **Validators reach it** within about 15 minutes of the start:

   ```bash
   ssh SSH_OPTS miner@<IP> 'sudo journalctl -u synth-miner --since=-20min --no-pager | grep -oE "Requesting UID: [0-9]+" | sort | uniq -c'
   ```

   This counts every caller, including those the blacklist then turns away. Step 6 below is the
   proof that answers are accepted.
6. **A validator accepted an answer:**
   `python3 <SKILL>/scripts/miner_status.py --uid <UID>` prints `latest answer: valid`. On a UID
   recycled from an evicted miner, the first verdicts can belong to the previous owner, so look for
   one dated after the start.
7. **No errors since the miner was ready.** The axon logs every rejected request as
   `<ErrorType>#<uuid>:`. Blacklist and protocol rejections are expected on an open port; anything
   else is a failed answer. `<MARK>` is `warm-up complete;` for a champion and
   `Miner starting at block` for the stock miner:

   ```bash
   ssh SSH_OPTS miner@<IP> 'sudo journalctl -u synth-miner -b --no-pager | awk "/<MARK>/{b=\"\"; next} {b=b \$0 \"\n\"} END{printf \"%s\", b}" | grep -E "[A-Za-z]+#[0-9a-f-]{36}:" | grep -vE "(Blacklisted|NotVerified|Priority|SynapseDendriteNone)Exception#|(UnknownSynapse|InvalidRequestName|SynapseParsing)Error#"'
   ```

   It should print nothing. For any line it prints, see "An error line" in
   [troubleshooting.md](troubleshooting.md).

The miner is live when 1 to 4 hold (4 for a champion), 6 shows a valid answer, and 7 prints
nothing. Report the UID, the IP, the model and its autoquant sha, then go on to step 8.

**The seed.** Each VM draws its own random `SYNTH_BENCHMARK_SEED` (also `PYTHONHASHSEED`) into
`/etc/synth-miner/miner.env` at provisioning, and keeps it for the life of the VM. Two deployments of
the same champion therefore answer with different paths, at the same expected CRPS.
On the VM, `grep -E '^(MODE|SYNTH_BENCHMARK_SEED)=' /etc/synth-miner/miner.env` shows it.

## 8. Wait for the dashboard

The dashboard (https://synthdata.co/miners/performance) lists a UID once a validator rewards round
includes it. Run this check in the background; it polls every 30 minutes for up to 4 hours, and
exits as soon as the UID has a `crypto-1h` row:

```bash
for i in $(seq 1 8); do out=$(python3 <SKILL>/scripts/miner_status.py --uid <UID> 2>&1); if printf '%s\n' "$out" | grep -qE '^crypto-1h +[0-9]+/'; then printf '%s\n' "$out"; exit 0; fi; sleep 1800; done; printf '%s\n' "$out"; echo "not on the crypto-1h leaderboard after 4 h"
```

- Tell the user the session can stay open meanwhile. If they leave, give them the
  `miner_status.py` command to run later.
- When it exits, report the rank, the dashboard URL and the UID's taostats page
  (https://taostats.io/subnets/50/metagraph).
- Still absent after 4 hours while step 7.6 shows valid answers: run `miner_status.py --scores`,
  and check [troubleshooting.md](troubleshooting.md).

What comes after:

- **First `crypto-1h` scores** arrive about 1–2 h after the first answer:
  `python3 <SKILL>/scripts/miner_status.py --uid <UID> --scores`.
- **First 24h scores** arrive after about 25–27 h.
- **Ramp-up.** Rewards stay small for about 5 days (`crypto-1h`) and 11 days (24h); see "What happens
  after registration" in [SKILL.md](../SKILL.md). Do not re-register.

Offer to run `miner_status.py --scores` again after 27 h.

## Later changes: FRED key, another model, tear-down

- **Add the FRED key later.** Save it on the laptop ("When the wallet is not ready", step 4), copy it
  as in step 5, then `sudo systemctl restart synth-miner`. The restart runs the warm-up again.
- **The VM installs once.** A change to a variable the startup script reads (model, autoquant ref,
  wallet, network, port, `binance_api_host`) only updates the instance metadata, and the script does
  nothing once provisioned. `name`, `zone` and `disk_size_gb` recreate the VM, and `machine_type`
  stops and starts it, so run `terraform plan` before any later apply. To change the model, the
  autoquant ref or any other setting, recreate the VM:
  1. Pick again (step 2), and update `champion` and `autoquant_ref` in `terraform.tfvars`.
  2. `GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" terraform -chdir=<REPO>/miner-setup/terraform/gcp apply -input=false -auto-approve -replace=google_compute_instance.miner`.
     The VM and its disk are deleted; the static IP stays.
  3. `ssh-keygen -R <IP>`: the new VM has a new host key.
  4. Steps 4 and 5 without the rehearsal, then step 7. Do not register again: `register.py --check`
     still shows the UID.

  The UID misses every prompt from the replace until the new miner has warmed up, and the new VM
  draws a new seed.
- **Tear down** with `GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" terraform -chdir=<REPO>/miner-setup/terraform/gcp destroy -input=false`.
  It deletes the VM, the IP and the firewall rules; the laptop keeps the wallet. The UID stays
  registered, scoring misses, until it is evicted.
- **AWS** works the same way from `miner-setup/terraform/aws`, with an Elastic IP, the SSH user
  `ubuntu` and an existing `key_pair_name`; see [deployment.md](deployment.md) §C.

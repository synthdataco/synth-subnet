# Validator

### Table Of Contents

- [1. Requirements](#1-requirements)
- [2. Setup](#2-setup)
- [3. Create a Wallet](#3-create-a-wallet)
- [4. Run the Validator](#4-run-the-validator)
- [5. Options](#5-options)
  - [5.1. Common Options](#51-common-options)
    - [`--axon.port INTEGER`](#--axonport-integer)
    - [`--cycle_interval_minutes.low INTEGER`](#--cycle_interval_minuteslow-integer)
    - [`--cycle_interval_minutes.high INTEGER`](#--cycle_interval_minuteshigh-integer)
    - [`--logging.debug`](#--loggingdebug)
    - [`--logging.info`](#--logginginfo)
    - [`--logging.trace`](#--loggingtrace)
    - [`--netuid INTEGER`](#--netuid-integer)
    - [`--neuron.axon_off BOOLEAN`](#--neuronaxon_off-boolean)
    - [`--neuron.device TEXT`](#--neurondevice-text)
    - [`--neuron.disable_set_weights BOOLEAN`](#--neurondisable_set_weights-boolean)
    - [`--neuron.dont_save_events BOOLEAN`](#--neurondont_save_events-boolean)
    - [`--neuron.epoch_length INTEGER`](#--neuronepoch_length-integer)
    - [`--neuron.events_retention_size TEXT`](#--neuronevents_retention_size-text)
    - [`--neuron.name TEXT`](#--neuronname-text)
    - [`--neuron.sample_size INTEGER`](#--neuronsample_size-integer)
    - [`--neuron.timeout INTEGER`](#--neurontimeout-integer)
    - [`--neuron.nprocs INTEGER`](#--neuronnprocs-integer)
    - [`--neuron.vpermit_tao_limit INTEGER`](#--neuronvpermit_tao_limit-integer)
    - [`--validator.assets TEXT`](#--validatorassets-text)
    - [`--wallet.hotkey TEXT`](#--wallethotkey-text)
    - [`--wallet.name TEXT`](#--walletname-text)
  - [5.2. Logging Options](#52-logging-options)
    - [`--gcp.log_id_prefix TEXT`](#--gcplog_id_prefix-text)
- [6. Appendix](#4-appendix)
  - [6.1. Useful Commands](#41-useful-commands)

### 1. Requirements

- [Git](https://github.com/git-guides/install-git)
- [Ubuntu v20.04+](https://ubuntu.com/download)

<sup>[Back to top ^][table-of-contents]</sup>

### 2. Setup

**Step 1: Clone the repository**

```shell
git clone https://github.com/synthdataco/synth-subnet.git
```

**Step 2: Change directory to the project root**

```shell
cd ./synth-subnet
```

**Step 3: Add the required repositories**

```shell
sudo add-apt-repository ppa:deadsnakes/ppa
```

> ⚠️ **NOTE:** The [deadsnakes](https://github.com/deadsnakes) repository, while unofficial, it is hugely popular and used by many Python projects.

**Step 4: Install Rust**

```shell
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

**Step 5: Install the dependencies**

```shell
sudo apt update && \
  sudo apt install nodejs npm python3.11 pkg-config
```

**Step 6: Install [PM2](https://pm2.io/)**

```shell
sudo npm install pm2 -g
```

**Step 7: Install [uv](https://docs.astral.sh/uv/)**

```shell
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

> ⚠️ **NOTE**: The second command puts `uv` on the `PATH` of the current shell; new shells pick it up automatically.

**Step 8: Install the Python environment**

From the repository root, create the virtual environment and install all locked dependencies:

```shell
uv sync
```

**Step 9: Activate and switch to the newly created Python virtual environment**

```shell
source .venv/bin/activate
```

This should activate the `.venv` environment and you should see the command line prefixed with `(.venv)`.

## 3. Create a Wallet

> 💡 **TIP:** For a more extensive list of the Bittensor CLI commands see [here](https://docs.bittensor.com/btcli).

**Step 1: Activate the Python virtual environment**

If you haven't already, ensure you are running from the Python virtual environment:

```shell
source .venv/bin/activate
```

**Step 2: Create the cold/hot wallets**

```shell
btcli wallet create \
  --wallet.name validator \
  --wallet.hotkey default
```

> 🚨 **WARNING:** You must ensure your wallets have enough TAO (0.1 should be sufficient) to be start mining. For testnet, you can use the [`btcli wallet faucet`](https://docs.bittensor.com/btcli#btcli-wallet-faucet).

**Step 3: Register wallet**

Acquire a slot on the Bittensor subnet by registering the wallet:

```shell
btcli subnet register \
  --wallet.name validator \
  --wallet.hotkey default \
  --netuid 50
```

if you want to try it on testnet first, run the following command:

```shell
btcli subnet register \
  --wallet.name validator \
  --wallet.hotkey default \
  --network test \
  --netuid 247
```

```shell
btcli root register --wallet.name validator --wallet.hotkey default
```

4. Stake:

```shell
btcli stake add \
  --wallet.name validator \
  --wallet.hotkey default \
  --netuid 50
```

for testnet:

```shell
btcli stake add \
  --wallet.name validator \
  --wallet.hotkey default \
  --network test \
  --netuid 247
```

**Step 4: Verify wallet registration (optional)**

Check the wallet has been registered:

```shell
btcli wallet overview \
  --wallet.name validator \
  --wallet.hotkey default
```

You can also check the network metagraph:

```shell
btcli subnet metagraph \
  --netuid 50
```

for testnet it's:

```shell
btcli subnet metagraph \
  --network test \
  --netuid 247
```

<sup>[Back to top ^][table-of-contents]</sup>

## 4. Run the Validator

**Step 1: Database setup**

- Create a postgres database with the name "synth"
- Rename the ".env.example" in the root of the repo to ".env"
- Update the `.env` file with your database credentials.

**Step 2: Activate the Python virtual environment**

```shell
source .venv/bin/activate
```

**Step 3: Run database migrations**

```shell
alembic upgrade head
```

**Step 4: Start PM2 with the validator config**

```shell
pm2 start validator.config.js
```

for testnet use:

```shell
pm2 start validator.test.config.js
```

**Step 5: Check the validator is running (optional)**

```shell
pm2 list
```

> 💡 **NOTE:** The validator queries miners in parallel via a multiprocess dendrite. The number of worker processes is controlled by [`--neuron.nprocs`](#--neuronnprocs-integer) (default `2`). The `1h` HFT competition sends prompts every ~10 min, so under-sized hosts can miss cycle deadlines — size `--neuron.nprocs` to roughly match the CPU cores available on the host.

<sup>[Back to top ^][table-of-contents]</sup>

## 5. Options

### 5.1. Common Options

#### `--axon.port INTEGER`

The external port for the Axon component. This port is used to communicate to other neurons.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--axon.port 8091",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --axon.port 8091
```

for testnet it's:

```shell
pm2 start validator.test.config.js -- --axon.port 8091
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--cycle_interval_minutes.low INTEGER`

Cycle interval in minutes for the 24h prompt.

Default: `5`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--cycle_interval_minutes.low 5",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --cycle_interval_minutes.low 5
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--cycle_interval_minutes.high INTEGER`

Cycle interval in minutes for the 1h prompt.

Default: `2`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--cycle_interval_minutes.high 2",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --cycle_interval_minutes.high 2
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--logging.debug`

Turn on bittensor debugging information.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--logging.debug",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --logging.debug
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--logging.info`

Turn on bittensor info level information.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--logging.info",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --logging.info
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--logging.trace`

Turn on bittensor trace level information.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--logging.trace",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --logging.trace
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--netuid INTEGER`

The netuid (network unique identifier) of the subnet within the root network, (e.g. 247).

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--netuid 247",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --netuid 247
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.axon_off BOOLEAN`

This will switch off the Axon component.

Default: `false`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.axon_off true",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.axon_off true
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.device TEXT`

The name of the device to run on.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.device cuda",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.device cuda
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.dont_save_events BOOLEAN`

Whether events are saved to a log file.

Default: `false`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.dont_save_events true",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.dont_save_events true
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.disable_set_weights BOOLEAN`

Disables setting weights.

Default: `false`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.disable_set_weights true",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.disable_set_weights true
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.epoch_length INTEGER`

The default epoch length (how often we set weights, measured in 12 second blocks), (e.g. 100).

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.epoch_length 100",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.epoch_length 100
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.events_retention_size TEXT`

The events retention size.

Default: `2147483648` (2GB)

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.events_retention_size 2147483648",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.events_retention_size 2147483648
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.name TEXT`

Trials for this neuron go in neuron.root / (wallet_cold - wallet_hot) / neuron.name.

Default: `validator`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.name validator",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.name validator
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.sample_size INTEGER`

The number of validators to query in a single step.

Default: `50`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.sample_size 50",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.sample_size 50
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.timeout INTEGER`

The maximum timeout in seconds of the validator neuron response, (e.g. 120).

Default: `-`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.timeout 120",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.timeout 120
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.nprocs INTEGER`

The number of processes to run for the validator dendrite, (e.g. 2).

Default: `2`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.nprocs 2",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.nprocs 2
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.vpermit_tao_limit INTEGER`

The maximum amount of miner stake allowed for the validator to process validator response, (e.g. 999999).

Default: `999999`

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--neuron.vpermit_tao_limit 999999",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --neuron.vpermit_tao_limit 999999
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--validator.assets TEXT`

Comma-separated list of assets to restrict the cycle to (e.g. `BTC,ETH`). A single asset without a comma also works (e.g. `BTC`). When unset, the validator cycles through the full asset list of the selected cycle (the default and recommended behavior).

Only applies to the `low_frequency` and `high_frequency` cycles. The `scoring` cycle ignores this flag.

If any requested asset is not part of the selected cycle's asset list, the validator exits at startup with a `ValueError` listing the unknown assets and the valid options.

Default: `""` (cycle through the full asset list)

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--validator.cycle_name low_frequency --validator.assets BTC,ETH",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --validator.assets BTC,ETH
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--wallet.hotkey TEXT`

The hotkey of the wallet.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--wallet.hotkey default",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --wallet.hotkey default
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--wallet.name TEXT`

The name of the wallet to use.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--wallet.name validator",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --wallet.name validator
```

<sup>[Back to top ^][table-of-contents]</sup>

### 5.2. Logging Options

#### `--gcp.log_id_prefix TEXT`

String to set the GCP log ID prefix.

Example:

```js
// validator.config.js
module.exports = {
  apps: [
    {
      name: "validator",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--gcp.log_id_prefix my_validator_name",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start validator.config.js -- --gcp.log_id_prefix my_validator_name
```

for testnet it's:

```shell
pm2 start validator.test.config.js -- --gcp.log_id_prefix my_validator_name
```

<sup>[Back to top ^][table-of-contents]</sup>

## 6. Appendix

### 6.1. Useful Commands

| Command                          | Description                     |
| -------------------------------- | ------------------------------- |
| `pm2 stop validator`             | Stops the validator.            |
| `pm2 logs validator --lines 100` | View the logs of the validator. |

<sup>[Back to top ^][table-of-contents]</sup>

<!-- links -->

[table-of-contents]: #table-of-contents

## Run the validator with Docker

You can run the validator with Docker and docker compose.

1. Run this command:

```
cp .env.validator .env.validator.local
```

2. Optionally edit "POSTGRES_PASSWORD" in `.env.validator.local` file. Don't change "POSTGRES_DB"
3. Optionally edit `entrypoint-validator.sh` file, especially `log_id_prefix` if you want to forward the log to a GCP Log Bucket. For this you need the credential file and environment variable GOOGLE_APPLICATION_CREDENTIALS set to the path of this file
4. Install docker

```
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo groupadd docker
sudo usermod -aG docker $USER
newgrp docker
```

5. Setup your bittensor wallet
6. Start: `docker compose up --build --remove-orphans --force-recreate -d`
7. Update with the same command
8. Read the logs: `docker compose logs -f`

### Useful commands

- Enter into the postgres container: `docker exec -it postgres bash`
- Enter into psql: `psql -U postgres`
- List databases: `\l`. You should see "synth" database
- Connect to database: `\c synth`

### Useful SQL queries

- Check the validator requests: `select * from validator_requests order by start_time desc limit 500;`
- Check the miner predictions:

```
select mp.format_validation, vr.start_time, miners.miner_uid, process_time
from miner_predictions mp
join validator_requests vr on mp.validator_requests_id = vr.id
join miners on miners.id = mp.miner_id
order by start_time desc
limit 500;
```

- Check the miner scores:

```
SELECT
	MS.ID,
	MP.VALIDATOR_REQUESTS_ID,
	MS.MINER_PREDICTIONS_ID,
	VR.START_TIME,
	VR.ASSET,
	MS.SCORED_TIME,
	MINERS.MINER_UID,
	(MS.SCORE_DETAILS_V3 -> 'total_crps')::FLOAT AS CSRP,
	MS.PROMPT_SCORE_V3
FROM
	MINER_SCORES MS
	JOIN MINER_PREDICTIONS MP ON MP.ID = MS.MINER_PREDICTIONS_ID
	JOIN MINERS ON MINERS.ID = MP.MINER_ID
	JOIN VALIDATOR_REQUESTS VR ON VR.ID = MP.VALIDATOR_REQUESTS_ID
WHERE
	MS.SCORED_TIME > NOW()::DATE - 10
ORDER BY
	MS.ID DESC;
```

- Check the predictions table size: `SELECT pg_size_pretty(pg_total_relation_size('miner_predictions')) as size;`
- /!\ Delete old predictions

```
delete from miner_predictions
using validator_requests
where miner_predictions.validator_requests_id = validator_requests.id
and validator_requests.start_time < now()::DATE - 20;
```

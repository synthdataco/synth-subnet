# Miner Reference

### Table of contents

- [1. Options](#1-options)
  - [1.1. Common Options](#11-common-options)
    - [`--axon.port INTEGER`](#--axonport-integer)
    - [`--blacklist.validator_min_stake INTEGER`](#--blacklistvalidator_min_stake-integer)
    - [`--logging.debug`](#--loggingdebug)
    - [`--logging.info`](#--logginginfo)
    - [`--logging.trace`](#--loggingtrace)
    - [`--netuid INTEGER`](#--netuid-integer)
    - [`--neuron.device TEXT`](#--neurondevice-text)
    - [`--neuron.dont_save_events BOOLEAN`](#--neurondont_save_events-boolean)
    - [`--neuron.epoch_length INTEGER`](#--neuronepoch_length-integer)
    - [`--neuron.events_retention_size TEXT`](#--neuronevents_retention_size-text)
    - [`--neuron.name TEXT`](#--neuronname-text)
    - [`--neuron.timeout INTEGER`](#--neurontimeout-integer)
    - [`--wallet.hotkey TEXT`](#--wallethotkey-text)
    - [`--wallet.name TEXT`](#--walletname-text)
  - [1.2. Weights & Bases Options](#12-weights--bases-options)
    - [`--wandb.enabled BOOLEAN`](#--wandbenabled-boolean)
    - [`--wandb.entity TEXT`](#--wandbentity-text)
    - [`--wandb.project_name TEXT`](#--wandbenabled-boolean)
- [2. Appendix](#2-appendix)
  - [2.1. FAQs](#21-faqs)
  - [2.2. Useful Commands](#22-useful-commands)
  - [2.3. Troubleshooting](#23-troubleshooting)

## 1. Options

### 1.1. Common Options

#### `--axon.port INTEGER`

The external port for the Axon component. This port is used to communicate to other neurons.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --axon.port 8091
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--blacklist.validator_min_stake INTEGER`

Minimum validator stake to accept forward requests from as a miner, (e.g. 65000).

Default: `65000`

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
      args: "--blacklist.validator_min_stake 65000",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start miner.config.js -- --blacklist.validator_min_stake 65000
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--logging.debug`

Turn on bittensor debugging information.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --logging.debug
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--logging.info`

Turn on bittensor info level information.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --logging.info
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--logging.trace`

Turn on bittensor trace level information.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --logging.trace
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--netuid INTEGER`

The netuid (network unique identifier) of the subnet within the root network: `50` on mainnet, `247` on testnet. The examples below use the testnet value.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --netuid 247
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.device TEXT`

The name of the device to run on.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --neuron.device cuda
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.dont_save_events BOOLEAN`

Whether events are saved to a log file.

Default: `false`

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --neuron.dont_save_events true
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.epoch_length INTEGER`

The default epoch length (how often we set weights, measured in 12 second blocks), (e.g. 100).

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --neuron.epoch_length 100
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.events_retention_size TEXT`

The events retention size.

Default: `2147483648` (2GB)

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --neuron.events_retention_size 2147483648
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.name TEXT`

Trials for this neuron go in neuron.root / (wallet_cold - wallet_hot) / neuron.name.

Default: `miner`

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
      args: "--neuron.name miner",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start miner.config.js -- --neuron.name miner
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--neuron.timeout INTEGER`

The maximum timeout in seconds of the miner neuron response, (e.g. 120).

Default: `-`

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --neuron.timeout 120
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--wallet.hotkey TEXT`

The hotkey of the wallet.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
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
pm2 start miner.config.js -- --wallet.hotkey default
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--wallet.name TEXT`

The name of the wallet to use.

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
      args: "--wallet.name miner",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start miner.config.js -- --wallet.name miner
```

<sup>[Back to top ^][table-of-contents]</sup>

### 1.2. Weights & Bases Options

#### `--wandb.enabled BOOLEAN`

Boolean toggle for wandb integration.

Default: `false`

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
      args: "--wandb.enabled true",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start miner.config.js -- --wandb.enabled true
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--wandb.entity TEXT`

The username or team name where you want to send W&B runs.

Default: `opentensor-dev`

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
      args: "--wandb.entity opentensor-dev",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start miner.config.js -- --wandb.entity opentensor-dev
```

<sup>[Back to top ^][table-of-contents]</sup>

#### `--wandb.project_name TEXT`

The name of the project where W&B runs.

Default: `template-miners`

Example:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
      args: "--wandb.project_name template-miners",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

Alternatively, you can add the args directly to the command:

```shell
pm2 start miner.config.js -- --wandb.project_name template-miners
```

<sup>[Back to top ^][table-of-contents]</sup>

## 2. Appendix

### 2.1. FAQs

#### 1. What is the best way for me to track my miner’s performance?

Visit the Synth Miner Dashboard: [https://synthdata.co/miners/performance](https://synthdata.co/miners/performance).

#### 2. How long do I have to wait for my new miner to get the first CRPS score?

Assuming your setup is correct, and you're submitting predictions, it depends on the competition: the CRPS is calculated against the actual prices over the prediction horizon, so a `crypto-1h` prediction gets its first score roughly 1–2 hours after submission, while `crypto-24h` and `com-equ-24h` predictions take approximately 25–27 hours.

#### 3. What does it mean when I check [https://api.synthdata.co/validation/miner?uid=](https://api.synthdata.co/validation/miner?uid=) and see: `{"validated":false,"reason":"Number of time points is incorrect: expected 289, got 288","response_time":"4.07"}`?

It means your submission has the wrong number of time points. The expected count depends on the prompt's competition (see FAQ #5): 289 points for the 24h competitions, 61 for `crypto-1h`. The error message always tells you the count expected for that prompt.

#### 4. Why does my miner keep getting a CRPS score of -1?

Your miner will receive a CRPS score of -1 if the prediction is not in the correct format or if the submission is late.

#### 5. What is the correct format for subnet-prompted predictions?

A prediction is considered valid if it meets all the following conditions:

- Submitted before the timeout specified by the validator.
- Contains the required number of simulation paths (num_simulations in the prompt).
- Each path has the correct number of time points:

```python
expected_time_points = (time_length / time_increment) + 1
```

Per competition (see the [competition table in the README](../README.md#12-task-presented-to-the-miners) for the asset lists):

| Competition   | `time_length` | `time_increment` | points per path |
| ------------- | ------------- | ---------------- | --------------- |
| `crypto-1h`   | 3600s         | 60s              | 61              |
| `crypto-24h`  | 86400s        | 300s             | 289             |
| `com-equ-24h` | 86400s        | 300s             | 289             |

The expected format is as follows:

```json
[
  start_timestamp, time_interval,
  [path1_price_t0, path1_price_t1, path1_price_t2, ...], [path2_price_t0, path2_price_t1, path2_price_t2, ...],
  ...
]
```

An example of a valid response would be:

```json
[
  1760084861, 300,
  [104856.23, 104972.01, 105354.9, ...], [104856.23, 104724.54, 104886, ...], [104856.23, 104900.12, 104950.45, ...]
  ...
]
```

where

- the first element is the timestamp of the start time of the prompt,
- second is the time increment of the prompt,
- then arrays of prices, where each point must be a finite number (no `NaN`/`Infinity`, no booleans) with at most 8 significant digits — magnitude and sign don't matter, e.g. `12345678.0`, `0.00012345678` and `1.2345e20` are all valid. Otherwise, the validator will reject the submission.
  - validation error messages: `Price format is incorrect: too many digits <point>`, `Price format is incorrect: non-finite value <point>`

You can find the validation function (`_point_error`) here:

[response_validation_v2.py](https://github.com/synthdataco/synth-subnet/blob/main/synth/validator/response_validation_v2.py)

And an example of the prompt parameters here:

[prompt_config.py#L5](https://github.com/synthdataco/synth-subnet/blob/main/synth/validator/prompt_config.py#L5)

You can test your prediction format using this miner script:

```shell
python synth/miner/run.py
```

Expected output:

```
start_time 2025-11-10T13:29:00+00:00
CORRECT
```

#### 6. Synth is predicting multiple assets. How does each asset prediction contribute to the smoothed score?

Each asset has its own prompts and is scored separately: for every asset/prompt pair, the validator computes a CRPS-based `prompt_score_v3` over time. These per-asset scores are then combined into a single smoothed score using a time-based moving average with asset-specific coefficients (weights). Assets with higher coefficients contribute more to the final smoothed score than lower-weighted assets.

#### 7. How do I improve my miner performance?

Synth regularly publishes detailed miner performance reviews highlighting the strategies of top-performing miners. These reviews can help you optimize your own models.

Explore them here: [https://synthdata.co/research](https://synthdata.co/research)

You can also use the backtester (FAQ #13) to measure whether a model change improves your rank.

#### 8. Do I need to run multiple miners?

If you're using the same model per asset, no — rewards will be the same. If you're using different models, then yes, it's encouraged to experiment and find what works best.

#### 9. Is there a penalty for turning off my miner for a period of time?

If you miss a prompt or submit in an incorrect format, your prompt score will be penalized—set to the 90th percentile of all prompt scores, and your CRPS will be -1.

If you make the best prediction, your prompt score will be 0, and your CRPS will also be at its minimum of 0, meaning your prediction perfectly matched the realized price.

The smoothed score is calculated as the moving average of your prompt scores over time.

These rules apply across all assets.

#### 10. How much time do miners have to respond to a prompt?

Each prompt contains a start_time, which acts as the deadline for your response. You’ll always have at least 1 minute from when the prompt is sent. We recommend submitting your response within 20 seconds.

#### 11. Is Synth running on a testnet too?

Yes. The testnet UID is 247, and it functions identically to mainnet mostly but can have feature previews and other experimental features.

We highly recommend running a testnet miner to test your model before deploying to mainnet.

#### 12. Once I start a miner using the guide on GitHub, where do I change the code to run my own model?

Modify this function:

[simulations.py#L25](https://github.com/synthdataco/synth-subnet/blob/main/synth/miner/simulations.py#L25)

Take into account all prompt parameters except sigma, which you may ignore.

#### 13. Can I test my model's accuracy without deploying to mainnet?

Yes — the open-source [synth-lib backtester](https://github.com/synthdataco/synth-lib) scores your prediction files with the same CRPS / smoothed-score / reward-weight logic the live validator runs, against real historical prices, and charts your rank evolution, CRPS distribution, and estimated earnings per competition. See the [synth-lib README](https://github.com/synthdataco/synth-lib#2-run-a-backtest) for the full guide and its [known caveats](https://github.com/synthdataco/synth-lib#known-caveats) for the earliest supported backtest window.

For a live end-to-end rehearsal of the network plumbing, also run a miner on testnet (FAQ #11).

<sup>[Back to top ^][table-of-contents]</sup>

### 2.2. Useful Commands

| Command                      | Description                 |
| ---------------------------- | --------------------------- |
| `pm2 stop miner`             | Stops the miner.            |
| `pm2 logs miner --lines 100` | View the logs of the miner. |

<sup>[Back to top ^][table-of-contents]</sup>

### 2.3. Troubleshooting

#### `ModuleNotFoundError: No module named 'simulation'`

This error is due to Python unable to locate the local Python modules. To avoid this error, ensure you have created and activate the Python virtual environment from the project root and ensure the `PYTHONPATH` is present in the `<miner|validator>.config.js` file and is pointing to the project root.

An example of a config file should be:

```js
// miner.config.js
module.exports = {
  apps: [
    {
      name: "miner",
      interpreter: "python3",
      script: "./neurons/miner.py",
      args: "--netuid 247 --logging.debug --logging.trace --subtensor.network test --wallet.name miner --wallet.hotkey default --axon.port 8091",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
```

As you can see, we are setting the `PYTHONPATH` environment variable that will be injected when we run `pm2 start miner`.

<sup>[Back to top ^][table-of-contents]</sup>

<!-- links -->

[table-of-contents]: #table-of-contents

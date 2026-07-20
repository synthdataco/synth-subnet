# Changelog

## Unreleased — scheduled for 2026-07-23

- Price feeds migrate off Pyth: validator ground-truth candles and the reference miner's spot price now come from **Binance spot** for BTC/ETH/SOL/XRP (`BTCUSDT`, …), **Hyperliquid spot** for HYPE (`HYPE/USDC`), and **Hyperliquid perps** for equities/commodities (`xyz:*`). The reference miner no longer uses `PYTH_API_KEY`, Pyth Lazer, or Pyth Hermes (Hermes remains only to serve SPYX prompts from not-yet-upgraded validators during the rollout)
- SPYX (tokenized SPY, Pyth) is retired and replaced by a new asset **SP500** (S&P 500 index, Hyperliquid `xyz:SP500`, ~10x SPYX's price level) in the Commodities/Equities 24h competition. Prompting swaps at deploy; in-flight SPYX requests are still scored from Pyth and SPYX scores age out of the 10-day moving-average window naturally. SP500 inherits SPYX's rolling-average weight (`3.437935601155441`)
- Validator: optional Pub/Sub notification for stored prediction cohorts.
- `BINANCE_API_HOST` process-env override (default `https://api.binance.com`): `api.binance.com` returns HTTP 451 from geo-restricted regions (e.g. the US) — set `https://data-api.binance.vision` (Binance's public market-data host) there. Validators and miners hosted in restricted regions need this to fetch BTC/ETH/SOL/XRP

## v1.10.3 — 2026-07-16

- 1h competition frequency increase for BTC and HYPE: each is now prompted every 1 minute.
- New `--validator.cycle_offset_minutes` flag for wall-clock lane scheduling ([#297](https://github.com/synthdataco/synth-subnet/pull/297))
- Validator perf: metagraph sync and `metagraph_history` snapshots moved to their own schedule ([#296](https://github.com/synthdataco/synth-subnet/pull/296))
- Validator perf: response validation vectorised; the price-format rule is redefined as "at most 8 significant digits" — strictly looser on real prices, stricter only on bools and non-finite values ([#301](https://github.com/synthdataco/synth-subnet/pull/301))
- Scoring: removed the p90 cap on prompt scores; missing responses are filled with the p95 score ([#302](https://github.com/synthdataco/synth-subnet/pull/302))
- Docs: added this CHANGELOG ([#299](https://github.com/synthdataco/synth-subnet/pull/299)); miner getting-started rework with backtesting step and 3-competition FAQs ([#300](https://github.com/synthdataco/synth-subnet/pull/300))

## v1.10.2 — 2026-07-09

- Public miner profiles: miners can attach a public identity to their coldkey (display name, avatar, social handles, website), shown on their profile page on the competitor dashboard. Metadata is submitted via a script and signed with the coldkey ([#295](https://github.com/synthdataco/synth-subnet/pull/295)) — see [the miner tutorial](https://github.com/synthdataco/synth-subnet/blob/main/docs/miner_tutorial.md#5-set-up-your-public-miner-profile)
- Validator deletes Bigtable prediction rows when the Postgres row is soft-deleted ([#293](https://github.com/synthdataco/synth-subnet/pull/293))

## v1.10.1 — 2026-07-07

Announced to miners on 2026-07-03, live 2026-07-07 at 1 PM UTC:

- SPCX coefficient updated to `0.4329342627683478` ([#291](https://github.com/synthdataco/synth-subnet/pull/291))
- 1h prompt timeout reduced by 60 seconds — miners should now expect a ~50 s timeout (`timeout_extra_seconds=0`, [#292](https://github.com/synthdataco/synth-subnet/pull/292))
- 1h competition frequency increase: prompts sent every 1 minute; with 5 assets, each individual asset is requested every 5 minutes
- 24h competition frequency increase: BTC requested every 4 minutes; the remaining 12 assets every 12 minutes (one asset per minute) — 75 prompts per hour overall across the 24h competition

Other changes in this release:

- Salted-hash keeper for density tapering ([#290](https://github.com/synthdataco/synth-subnet/pull/290))
- Data retention adjusted: low frequency 11 days, high frequency 6 days ([#294](https://github.com/synthdataco/synth-subnet/pull/294))
- Bump msgpack 1.2.0 → 1.2.1 ([#289](https://github.com/synthdataco/synth-subnet/pull/289))

## 2026-07-01 · Prompt frequency increase

Operational change (no tag):

- 1h competition prompts increased to every 2 minutes

## v1.10.0 — 2026-06-23 · Split in 3 competitions

**Miner action required**: update the blacklist function in `neurons/miner.py`.

Rewards are now split across 3 competitions, each with its own leaderboard and reward allocation, so miners can specialize in crypto forecasting or equity/commodity forecasting instead of participating in both:

- **Crypto 1h**: BTC, ETH, SOL, XRP, HYPE
- **Crypto 24h**: BTC, ETH, SOL, XRP, HYPE
- **Equities & Commodities 24h**: XAU, SPY, NVDA, GOOGL, TSLA, AAPL, WTIOIL, SPCX

Asset updates: XAU removed from the 1h competition; XRP added to the crypto competitions; SPCX added to Equities & Commodities 24h — priced via Hyperliquid or the [`Pyth.HL.SPCX/USDC` feed](https://app.pyth.com/explore/Pyth.HL.SPCX%2FUSDC).

- Split scoring and emissions into 3 competitions, and add SPCX ([#283](https://github.com/synthdataco/synth-subnet/pull/283))
- Miner blacklist rejects unsigned requests ([#282](https://github.com/synthdataco/synth-subnet/pull/282)), blacklist and docs updated ([#284](https://github.com/synthdataco/synth-subnet/pull/284))
- Refactor IP registration and validator docs ([#285](https://github.com/synthdataco/synth-subnet/pull/285))
- Enable bittensor CLI arg parsing (`BT_NO_PARSE_CLI_ARGS`) ([#286](https://github.com/synthdataco/synth-subnet/pull/286))
- QA test coverage for the 3-competition split ([#287](https://github.com/synthdataco/synth-subnet/pull/287))
- Fix miner XAU price feed id ([#288](https://github.com/synthdataco/synth-subnet/pull/288))

## 2026-06-17 · Prompt frequency increase

Operational change (no tag), announced 2026-06-15, effective 3 PM UTC:

- 24h competition prompts increased from one every 4 minutes to one every 3 minutes

**Miner action required**:

- Whitelist the validator IPs `35.195.33.228`, `34.45.197.133`, `167.150.153.50` and deny requests from any other IP
- Enforce the stake limit and validator permit to protect predictions: `--blacklist.force_validator_permit true --blacklist.validator_min_stake 65000`

## v1.9.3 — 2026-06-15

- Switch dependency management to `uv` ([#278](https://github.com/synthdataco/synth-subnet/pull/278))
- Silence noisy loggers ([#279](https://github.com/synthdataco/synth-subnet/pull/279))
- Validator config update ([#280](https://github.com/synthdataco/synth-subnet/pull/280))

## v1.9.2 — 2026-06-08

- Rename Bigtable env vars to `BIGTABLE_TABLE_PREDICTION_` ([#276](https://github.com/synthdataco/synth-subnet/pull/276))
- Remove debug logs ([#277](https://github.com/synthdataco/synth-subnet/pull/277))

## v1.9.1 — 2026-06-08

- Bigtable storage backend for miner predictions (`--storage.backend`) ([#265](https://github.com/synthdataco/synth-subnet/pull/265))
- Chunk Bigtable `mutate_rows` to stay under the 260 MiB RPC limit ([#272](https://github.com/synthdataco/synth-subnet/pull/272))
- `cycle_interval_minutes` CLI args ([#266](https://github.com/synthdataco/synth-subnet/pull/266))
- Keep only totals in `detailed_crps_data` ([#273](https://github.com/synthdataco/synth-subnet/pull/273))
- Tests: mock miner price fetch, drop validator-side `PYTH_API_KEY` ([#269](https://github.com/synthdataco/synth-subnet/pull/269))

## 2026-06-03 · Prompt frequency increase

Operational change (no tag), effective 3 PM CET:

- 24h competition prompts increased to one every 4 minutes
- 1h competition prompts unchanged at one every 3 minutes
- Overall: 35 prompts per hour

## v1.9.0 — 2026-05-22

- Pyth Pro price provider and wait-for-next-candle logic ([#260](https://github.com/synthdataco/synth-subnet/pull/260))
- Density tapering on predictions ([#263](https://github.com/synthdataco/synth-subnet/pull/263))
- Enforce total per-miner timeout in dendrite forward ([#253](https://github.com/synthdataco/synth-subnet/pull/253))
- New cycle interval logic ([#262](https://github.com/synthdataco/synth-subnet/pull/262)) and `assets` param ([#261](https://github.com/synthdataco/synth-subnet/pull/261))
- Miner setup with Terraform, Docker, and Ansible ([#259](https://github.com/synthdataco/synth-subnet/pull/259))
- Database index updates ([#264](https://github.com/synthdataco/synth-subnet/pull/264)), README/docs improvements ([#257](https://github.com/synthdataco/synth-subnet/pull/257), [#258](https://github.com/synthdataco/synth-subnet/pull/258))

## v1.8.3 — 2026-04-22

- Fix wrong logging level switch to warning ([#248](https://github.com/synthdataco/synth-subnet/pull/248))

## v1.8.2 — 2026-04-14

- Repository moved to the `synthdataco` organization ([#244](https://github.com/synthdataco/synth-subnet/pull/244))
- Better error handling ([#241](https://github.com/synthdataco/synth-subnet/pull/241))
- Fix missing realized last price ([#247](https://github.com/synthdataco/synth-subnet/pull/247))
- README updates ([#245](https://github.com/synthdataco/synth-subnet/pull/245), [#246](https://github.com/synthdataco/synth-subnet/pull/246))

## v1.8.1 — 2026-04-07 · Schedule new assets

- Reschedule the 3 new assets ([#243](https://github.com/synthdataco/synth-subnet/pull/243))
- Weighted metamodel research ([#242](https://github.com/synthdataco/synth-subnet/pull/242))

## v1.8.0 — 2026-04-06 · Add XRP, HYPE and WTIOIL

- Add XRP, HYPE, and WTIOIL assets ([#239](https://github.com/synthdataco/synth-subnet/pull/239))
- Data retention options extracted as configuration params ([#240](https://github.com/synthdataco/synth-subnet/pull/240))
- Fix `on_conflict_do_update` to use the `excluded` construct ([#238](https://github.com/synthdataco/synth-subnet/pull/238))
- Repo cleanup ([#236](https://github.com/synthdataco/synth-subnet/pull/236))

## v1.7.8 — 2026-03-20

- Miner scores request time improvements ([#235](https://github.com/synthdataco/synth-subnet/pull/235))

## v1.7.7 — 2026-03-12

- SMA days configurable via env var ([#234](https://github.com/synthdataco/synth-subnet/pull/234))

## v1.7.6 — 2026-03-11

- Fix gap interval scoring: suffix mismatch and row/column slice bug ([#227](https://github.com/synthdataco/synth-subnet/pull/227))
- Revert "remove scoring parameters" ([#226](https://github.com/synthdataco/synth-subnet/pull/226))
- Add database indexes, update README ([#224](https://github.com/synthdataco/synth-subnet/pull/224))
- Code cleanup around the equities launch ([#228](https://github.com/synthdataco/synth-subnet/pull/228))

## v1.7.5 — 2026-02-10

- Shared-memory fix to reduce memory consumption in the multiprocess environment ([#218](https://github.com/synthdataco/synth-subnet/pull/218))
- Light mode that erases old predictions (`--validator.mode light` on the scoring process) ([#221](https://github.com/synthdataco/synth-subnet/pull/221))
- README update ([#223](https://github.com/synthdataco/synth-subnet/pull/223))

## v1.7.4 — 2026-01-25

- Metagraph sync for low- and high-frequency jobs ([#220](https://github.com/synthdataco/synth-subnet/pull/220))
- Miner code fixes for the new bittensor version ([#219](https://github.com/synthdataco/synth-subnet/pull/219))

## v1.7.3 — 2026-01-22

- Schedule more equities ([#217](https://github.com/synthdataco/synth-subnet/pull/217))
- pm2 config update ([#216](https://github.com/synthdataco/synth-subnet/pull/216))

## v1.7.2 — 2026-01-22

- Restore sync dendrite multiprocess ([#215](https://github.com/synthdataco/synth-subnet/pull/215))
- Validators now run 3 pm2 processes (see `entrypoint.sh` for reference configs)

## v1.7.1 — 2026-01-20

- Full async cycles ([#212](https://github.com/synthdataco/synth-subnet/pull/212)), rolled back immediately via `first_run` in `AsyncScheduler` ([#214](https://github.com/synthdataco/synth-subnet/pull/214))
- Ignore "Response received after" errors ([#211](https://github.com/synthdataco/synth-subnet/pull/211))

## v1.7.0 — 2026-01-19 · Equities Launch

- Add 5 equities and schedule their launch ([#205](https://github.com/synthdataco/synth-subnet/pull/205), [#208](https://github.com/synthdataco/synth-subnet/pull/208))
- Timer-based scheduling ([#206](https://github.com/synthdataco/synth-subnet/pull/206), hotfix [#207](https://github.com/synthdataco/synth-subnet/pull/207))
- Scale scoring and prepare launch ([#209](https://github.com/synthdataco/synth-subnet/pull/209))
- Remove scoring parameters `ewma.window_days`, `softmax.beta`, `neuron.nprocs` ([#204](https://github.com/synthdataco/synth-subnet/pull/204))
- Add database indexes ([#203](https://github.com/synthdataco/synth-subnet/pull/203)), `logging.exception` refactor ([#200](https://github.com/synthdataco/synth-subnet/pull/200))

## v1.6.4 — 2025-12-20

- Remove rank, trust, and pruning score ([#202](https://github.com/synthdataco/synth-subnet/pull/202))

## v1.6.3 — 2025-12-17

- Switch gold feed to `Crypto.XAUT/USD` ([#198](https://github.com/synthdataco/synth-subnet/pull/198))
- Change `window_days` for the high-frequency competition ([#199](https://github.com/synthdataco/synth-subnet/pull/199))
- Refactor `should_skip_xau` ([#201](https://github.com/synthdataco/synth-subnet/pull/201))

## v1.6.2 — 2025-12-11

- Refactor and fix gold skipping ([#196](https://github.com/synthdataco/synth-subnet/pull/196))
- Default `softmax.beta` changed to `-0.2` ([#197](https://github.com/synthdataco/synth-subnet/pull/197))

## v1.6.1 — 2025-12-02

- Fix scheduled launch of the high-frequency cycle ([#194](https://github.com/synthdataco/synth-subnet/pull/194))

## v1.6.0 — 2025-12-02 · Schedule HFT

- High-frequency (1-hour) prompts ([#178](https://github.com/synthdataco/synth-subnet/pull/178))
- Validator updates ([#191](https://github.com/synthdataco/synth-subnet/pull/191)), README ([#190](https://github.com/synthdataco/synth-subnet/pull/190))
- Config: removed `--ewma.cutoff_days`, `--ewma.window_days`, `--neuron.use_multiprocess`; `--softmax.beta` set to `-0.05`

## v1.5.4 — 2025-11-21

- Skip scoring during the Cloudflare outage ([#189](https://github.com/synthdataco/synth-subnet/pull/189))

## v1.5.3 — 2025-11-19

- Fix gold score on weekends ([#188](https://github.com/synthdataco/synth-subnet/pull/188))

## v1.5.2 — 2025-11-17

- Hotfix validation frequency ([#186](https://github.com/synthdataco/synth-subnet/pull/186))

## v1.5.1 — 2025-11-12

- Hotfix: catch error in format validation ([#185](https://github.com/synthdataco/synth-subnet/pull/185))

## v1.5.0 — 2025-11-10 · New Format and 1k Paths

- New prediction format and scheduled rollout ([#174](https://github.com/synthdataco/synth-subnet/pull/174), [#179](https://github.com/synthdataco/synth-subnet/pull/179)); miners now submit 1,000 paths
- Store the real path in the validator requests table ([#173](https://github.com/synthdataco/synth-subnet/pull/173))
- Smoothed score with per-asset weights ([#181](https://github.com/synthdataco/synth-subnet/pull/181)), README formula update ([#180](https://github.com/synthdataco/synth-subnet/pull/180))
- Fix gold scoring with the new format ([#182](https://github.com/synthdataco/synth-subnet/pull/182)), hotfix immune score ([#183](https://github.com/synthdataco/synth-subnet/pull/183))
- Remove Slack logger ([#176](https://github.com/synthdataco/synth-subnet/pull/176)), rework `prepare_df_for_moving_average` ([#177](https://github.com/synthdataco/synth-subnet/pull/177))
- Config: `--ewma.half_life_days` replaced by `--ewma.window_days 10`

## v1.4.7 — 2025-10-17

- Split emissions to prepare the 1-hour prompt ([#175](https://github.com/synthdataco/synth-subnet/pull/175))

## v1.4.6 — 2025-09-09

- Update to bittensor v9.9.0 ([#172](https://github.com/synthdataco/synth-subnet/pull/172))
- Docker setup update ([#168](https://github.com/synthdataco/synth-subnet/pull/168)); new optional `NETWORK` env var
- Miner retries `get_asset_price` from Pyth

## v1.4.5 — 2025-08-29

- Retry inserts and increase database timeout in Docker ([#167](https://github.com/synthdataco/synth-subnet/pull/167))
- Fix NaN in scores ([#171](https://github.com/synthdataco/synth-subnet/pull/171))

## v1.4.4 — 2025-07-29 · SOL

- Schedule the launch of Solana (SOL) prompts ([#164](https://github.com/synthdataco/synth-subnet/pull/164))
- Add `deleted_at` column on predictions ([#162](https://github.com/synthdataco/synth-subnet/pull/162)), retry `insert_new_miners` ([#163](https://github.com/synthdataco/synth-subnet/pull/163))
- Handle price fetch errors ([#160](https://github.com/synthdataco/synth-subnet/pull/160)), add `use-multiprocess` config ([#159](https://github.com/synthdataco/synth-subnet/pull/159))
- Docs: miner tutorial ([#158](https://github.com/synthdataco/synth-subnet/pull/158)), testnet miner/validator docs ([#166](https://github.com/synthdataco/synth-subnet/pull/166)), new logo ([#161](https://github.com/synthdataco/synth-subnet/pull/161))

## v1.4.3 — 2025-07-12

- Fix gold skipping ([#156](https://github.com/synthdataco/synth-subnet/pull/156))
- Consensus config flags documented: `--ewma.half_life_days 5`, `--ewma.cutoff_days 10`, `--softmax.beta -0.1`, `--neuron.vpermit_tao_limit 999999`

## v1.4.2 — 2025-07-07 · Gold

- Schedule the launch of Gold (XAU) prompts ([#151](https://github.com/synthdataco/synth-subnet/pull/151), [#154](https://github.com/synthdataco/synth-subnet/pull/154))
- Timewatch on forward ([#150](https://github.com/synthdataco/synth-subnet/pull/150)), `neuron.nprocs` config ([#153](https://github.com/synthdataco/synth-subnet/pull/153))
- Fix request time when skipping gold ([#155](https://github.com/synthdataco/synth-subnet/pull/155)), log prefix in Slack messages ([#152](https://github.com/synthdataco/synth-subnet/pull/152))

## v1.4.1 — 2025-06-20

- Dendrite with multiprocess ([#148](https://github.com/synthdataco/synth-subnet/pull/148))
- Fix GCP logging ([#149](https://github.com/synthdataco/synth-subnet/pull/149)), Docker config update ([#147](https://github.com/synthdataco/synth-subnet/pull/147))
- README updated with the new scoring system, including ETH ([#146](https://github.com/synthdataco/synth-subnet/pull/146))

## v1.3.3 — 2025-05-27

- Slack alerts on runtime errors (`SLACK_TOKEN` / `SLACK_CHANNEL_ID`) and log forwarding to GCP Cloud Logging (`--gcp.log_id_prefix`) ([#142](https://github.com/synthdataco/synth-subnet/pull/142))
- Wandb removed from the validator
- Axon: 16-chunk prompt request ([#102](https://github.com/synthdataco/synth-subnet/pull/102))
- Score unique constraint with on-conflict update ([#139](https://github.com/synthdataco/synth-subnet/pull/139)), remove score duplicates ([#133](https://github.com/synthdataco/synth-subnet/pull/133))
- Add `created_at`/`updated_at` columns with server defaults ([#132](https://github.com/synthdataco/synth-subnet/pull/132)), IP address in metagraph history ([#138](https://github.com/synthdataco/synth-subnet/pull/138))
- Synth logo replaces Mode logo ([#141](https://github.com/synthdataco/synth-subnet/pull/141))

## v1.3.1 — 2025-05-14

- Hotfix BTC prompt frequency ([#136](https://github.com/synthdataco/synth-subnet/pull/136))

## v1.3.0 — 2025-05-14 · ETH Launch

- Schedule the launch of ETH prompts, alternating with BTC ([#135](https://github.com/synthdataco/synth-subnet/pull/135))
- CRPS calculation update: interval CRPS computed on relative price changes in basis points; 344 interval scores plus 1 absolute final-price score summed per prompt; prompt scores capped at the 90th-percentile CRPS sum and normalized against the best miner
- Log level adjustments ([#134](https://github.com/synthdataco/synth-subnet/pull/134))

## v1.2.2 — 2025-05-06

- Fix duplicate scores ([#131](https://github.com/synthdataco/synth-subnet/pull/131))
- `--softmax.beta` changed to `-0.0475`

## v1.2.1 — 2025-05-02 · Prompt score v3

- Prompt score v3 with backfill and changed CRPS calculation
- Insert-or-update scores to avoid duplicates

## v1.2.0 — 2025-05-02 · Multi-asset preparation

- New forward algorithm: two async tasks, one for prompting and one for scoring
- Refactors to support multiple assets and multiple simulations (price provider token param, prompt-score rename, alchemy models)
- Bittensor library update; removed `num_concurrent_forwards` param

## v1.1.6 — 2025-04-23

- Maintenance release (no code changes listed)

## v1.1.5 — 2025-04-22

- Absolute price CRPS calculation with gaps ([#96](https://github.com/synthdataco/synth-subnet/pull/96))
- Update validator whitelist ([#122](https://github.com/synthdataco/synth-subnet/pull/122))
- Add flake8 and mypy linters ([#119](https://github.com/synthdataco/synth-subnet/pull/119))

## v1.1.4 — 2025-04-07

- Fix softmax applied after UID filter ([#121](https://github.com/synthdataco/synth-subnet/pull/121))
- Fetch the real price once ([#118](https://github.com/synthdataco/synth-subnet/pull/118)), get UID for score from predictions ([#117](https://github.com/synthdataco/synth-subnet/pull/117))
- Config: `--ewma.half_life_days 3.5`, `--ewma.cutoff_days 7`, `--softmax.beta -0.005`

## v1.1.3 — 2025-04-03

- New miner registration flow ([#112](https://github.com/synthdataco/synth-subnet/pull/112))

## v1.1.2 — 2025-03-20

- Hotfix: remove old prediction table, add on-delete cascade ([#115](https://github.com/synthdataco/synth-subnet/pull/115))
- Validator whitelist update ([#114](https://github.com/synthdataco/synth-subnet/pull/114))

## v1.1.1 — 2025-03-17

- Rework validator auto-restart ([#111](https://github.com/synthdataco/synth-subnet/pull/111))
- Shuffle miners globally ([#110](https://github.com/synthdataco/synth-subnet/pull/110))

## v1.1.0 — 2025-03-13

- Rewards system v2 ([#104](https://github.com/synthdataco/synth-subnet/pull/104))
- Rollback miner shuffle ([#108](https://github.com/synthdataco/synth-subnet/pull/108))
- Config: removed `--ewma.alpha`, `--softmax.beta` set to `-0.003`

## v1.0.0 — 2025-03-12 · Initial release

First public release of the Synth subnet: miner and validator neurons, the simulation library and protocol definition, CRPS-based reward calculation on BTC price-path predictions, Postgres-backed miner data handling, and pm2/Docker deployment configs.

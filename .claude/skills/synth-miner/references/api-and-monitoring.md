# Public APIs and dashboards

Contents:
- Etiquette and quirks
- Synth API endpoints for miners
- Monitoring API
- Recipes

No API key is needed for anything here. The two APIs:
- **Synth API:** https://api.synthdata.co (docs at https://api.synthdata.co/docs).
- **Monitoring API:** https://monitoring.synthdata.co. Every path below starts with `/v1`; the
  schema is at https://monitoring.synthdata.co/v1/openapi.json.

Both report **mainnet** (netuid 50). Dashboards: https://synthdata.co/miners/performance and
https://taostats.io/subnets/50/metagraph.

## Etiquette and quirks

- **Pace requests.**
  - The APIs are rate-limited: synth-lib's README says a couple of requests per second on the
    no-auth endpoints.
  - Space requests out, back off on HTTP 429, and run one client at a time.
  - For long histories, use synth-lib's offline bundles
    ([model-and-backtesting.md](model-and-backtesting.md)), which page and cache for you.
- **Always pair the format parameters:** `time_length=3600` with `time_increment=60`, and
  `time_length=86400` with `time_increment=300`. `time_increment` defaults to 300, so a bare
  `time_length=3600` asks for a combination that does not exist.
- **Date parameters** accept `YYYY-MM-DD` or RFC 3339. Ranges are capped and `to` covers the whole
  UTC day. A range that is too long returns HTTP 400 with the limit in the message.
- **"No data" looks different per endpoint.**
  - Most endpoints answer HTTP 404 with a message. That does not always mean your query is wrong;
    there may be no data yet.
  - `/validation/miner` answers HTTP 500 for a UID without predictions.
  - `/v2/leaderboard/latest` can answer HTTP 200 with `null` for a competition the newest rewards
    round has not reached yet, so do not read `null` as "not ranked".
- **Data is keyed by UID.** A recycled UID returns its previous holders' rows too. Filter by your
  registration time from `/miners/registrations/historical`.
- **Mind the volume.** `/rewards/scores` for every miner over one day is tens of megabytes. Filter
  with `miner_uid` unless you really need the whole field.

## Synth API endpoints for miners

| Endpoint                                                                                     | What it returns                                                                                                                                                                                |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /validation/miner?uid=<UID>`                                                            | `{validated, reason, response_time}` for the UID's latest prediction. A valid answer has `validated: true` and an empty `reason`; otherwise `reason` is the validator's message                |
| `GET /validation/scores/historical?from=&to=&miner_uid=&asset=&time_increment=&time_length=` | Per-prompt rows: `miner_uid, asset, crps, prompt_score, vol_crps, scored_time, time_length`. `crps = -1` is a miss. The range is a couple of days                                              |
| `GET /validation/scores/latest?asset=&time_increment=&time_length=`                          | Every miner's row for the latest scored prompt of that asset and format                                                                                                                        |
| `GET /validation/prompts?from=&to=&asset=&time_increment=&time_length=`                      | `{start_times: [...]}`: every prompt sent, scored or not. Both format parameters are required                                                                                                  |
| `GET /validation/realized-path?start_time=&asset=&time_increment=&time_length=`              | `real_prices`: the exact path the validator scored, with `null` for venue gaps. `start_time` must be an exact prompt start (from `/validation/prompts`)                                        |
| `GET /rewards/scores?from=&to=&miner_uid=&competition=`                                      | Per reward round: `smoothed_score, reward_weight, prompt_name, updated_at`. Up to 7 days. `competition` is `crypto-1h`, `crypto-24h` or `com-equ-24h`; without it, Synth Ultra rows appear too |
| `GET /v2/leaderboard/latest?prompt_name=<competition>`                                       | Every miner's `rewards` (the reward weight) in the newest round, highest first. Can be `null`, see above                                                                                       |
| `GET /v2/leaderboard/historical?start_time=&end_time=&prompt_name=`                          | Reward weight per round over a period                                                                                                                                                          |
| `GET /v2/meta-leaderboard/latest?days=&prompt_name=`                                         | Reward weight summed per UID over the last `days`                                                                                                                                              |
| `GET /miners/registrations/historical?from=&to=`                                             | `{miner_uid, hotkey, created_at}`: when each hotkey appeared on each UID. Up to 30 days                                                                                                        |

Examples:

```bash
curl -s "https://api.synthdata.co/validation/miner?uid=<UID>"
curl -s "https://api.synthdata.co/validation/scores/historical?from=2026-10-25&to=2026-10-25&miner_uid=<UID>&asset=BTC&time_increment=60&time_length=3600"
curl -s "https://api.synthdata.co/validation/scores/latest?asset=SP500&time_increment=300&time_length=86400"
curl -s "https://api.synthdata.co/validation/prompts?from=2026-10-25&to=2026-10-25&asset=BTC&time_increment=300&time_length=86400"
curl -s "https://api.synthdata.co/validation/realized-path?start_time=<EXACT_START>&asset=BTC&time_increment=300&time_length=86400"
curl -s "https://api.synthdata.co/rewards/scores?from=2026-10-20&to=2026-10-25&miner_uid=<UID>&competition=crypto-24h"
```

**Synth Ultra rows.** Without a `competition` filter, `/rewards/scores` also returns Synth Ultra
rows, labelled:
- `VHFT 10s` for BTC;
- `VHFT 10s ETH`, `VHFT 10s SOL` and `VHFT 10s XRP` for the others.

The API docs do not mention them, so the format may change.
- `smoothed_score` is the field-relative mean CRPS, in that asset's price units. Lower is better.
- `reward_weight` is the UID's share of the miner block from that asset.
- Rank is the order of `smoothed_score`, ascending, at the latest `updated_at` of a label.

## Monitoring API

The monitoring API precomputes the dashboard's views; check `updated_at` in each answer.
Competitions are `crypto-1h`, `crypto-24h` and `com-equ-24h`, and leaderboards also accept
`asset=Overall`.

| Endpoint                                                                            | What it returns                                                                                            |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `GET /v1/profiles/{coldkey}`                                                        | Current and past UIDs, standings per competition, per-asset ranks, reward share, earnings, badges          |
| `GET /v1/competitions/{competition}/leaderboard?asset=Overall&page=1&page_size=500` | `rank`, `miner_uid`, `smoothed_score`, `reward_weight` and registration date for every miner, plus `total` |
| `GET /v1/competitions/{competition}/leaderboard/series?days=10`                     | Rank, smoothed score and reward weight over time for each miner                                            |
| `GET /v1/competitions/{competition}/assets/{asset}/miners/{uid}/series?days=10`     | Per prompt: `crps`, `prompt_score`, `rank`, `gap_to_top1`, `gap_to_top10`, `missed`                        |
| `GET /v1/competitions/{competition}/miners/{uid}/asset-ranks?days=10`               | Rank on each asset's board over time                                                                       |
| `GET /v1/competitions/{competition}/assets/{asset}/scoreboard?days=10`              | Prompt-score series of the miners holding ranks 1, 2, 3, 10, 25, 50, 100 and 200                           |
| `GET /v1/competitions/{competition}/assets/{asset}/snapshot?scored_time=<RFC3339>`  | The whole field on one scored prompt                                                                       |

- **An asset outside the competition returns 400.** An unknown competition returns 404.
- **`"source": "wip"` with empty rows** means a new competition, a new asset or an unknown coldkey.
  Data appears after the first scored prompts.

## Recipes

- **Status of one UID:** `scripts/miner_status.py --uid <UID>` shows:
  - the validation verdict;
  - rank, weight and smoothed score from the monitoring leaderboards;
  - any Synth Ultra rows.

  Add `--scores` for per-asset misses. It paces itself.
- **Is my miner answering?**
  1. Check `/validation/miner?uid=<UID>`.
  2. Then count `crps == -1` rows per asset in `/validation/scores/historical`.
- **Where am I losing?**
  - Compare your `prompt_score` per asset (0 is best) with the field in `/validation/scores/latest`,
    or use the monitoring `series` endpoint (`gap_to_top1`, `gap_to_top10`).
  - High-coefficient assets weigh most in the rolling score (see [scoring.md](scoring.md)).
- **Is my anchor right?**
  1. Get exact start times from `/validation/prompts`.
  2. Fetch `/validation/realized-path` for one of them.
  3. Compare `real_prices[0]` with your `path[0]` for the same prompt.
- **Is `crypto-1h` hurting on volatility?** `vol_crps` in `/validation/scores/historical` is the
  volatility part already included in `crps`. Judge it only against the field on the same prompts;
  see "Diagnosing `vol_crps`" in [model-and-backtesting.md](model-and-backtesting.md).
- **How top-heavy is a competition right now?** Take the `reward_weight` column of the monitoring
  leaderboard and compare the top UIDs with the median.
- **When do prompts arrive?** `/validation/prompts` for each asset and format gives the start
  times. Arrival is 60 s earlier for 1h and 120 s earlier for 24h.

"""Read-only Layer-2 soak check for the competition split.

Run with the SAME environment as the validator (Postgres creds:
POSTGRES_USER/PASSWORD/HOST/PORT/DB, and PYTHONPATH=.). Reads only — plain
SELECTs against the validator's Postgres, no writes.

Verifies, against live validator data:
  - per-competition reward_weight sums for the latest scoring cycle
    (each `prompt_name` should be ~SMOOTHED_SCORE_COEFFICIENT, a VHFT
    asset's that divided by the number of assets in VHFT_REWARDED_ASSETS;
    VHFT is expected only when VHFT_SCORES_URL is set, as for the validator);
  - recent miner_scores volume per (asset, time_length) competition slice;
  - the most recent weights_update_history rows resolved SUCCESS.

    python scripts/check_competition_rewards.py
"""

from dotenv import load_dotenv
from sqlalchemy import text

from synth.validator import competition_config
from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.vhft_score_provider import VhftScoreProvider

SCORES_LOOKBACK_HOURS = 24
WEIGHTS_TO_SHOW = 5


def main() -> None:
    load_dotenv()
    engine = MinerDataHandler().engine
    # The same VHFT configuration the validator reads, so a skipped asset is
    # still counted and an unrewarded one is not expected.
    vhft = VhftScoreProvider.from_env()
    shares = competition_config.competition_shares(
        vhft.assets if vhft is not None else []
    )
    labels = set(shares)

    with engine.connect() as conn:
        # --- per-competition reward_weight sums for the latest cycle ---
        latest = conn.execute(
            text("SELECT max(updated_at) FROM miner_rewards")
        ).scalar()
        print(f"latest miner_rewards.updated_at: {latest}")
        print(
            f"\n{'prompt_name':28} {'#miners':>8} {'Σ reward_weight':>16} "
            f"{'vs share':>10}"
        )
        print("-" * 66)
        rows = conn.execute(
            text("""
                SELECT prompt_name,
                       count(DISTINCT miner_id) AS n,
                       sum(reward_weight) AS total
                FROM miner_rewards
                WHERE updated_at = :latest
                GROUP BY prompt_name
                ORDER BY prompt_name
                """),
            {"latest": latest},
        ).fetchall()
        for r in rows:
            flag = "" if r.prompt_name in labels else "  <-- UNKNOWN LABEL"
            delta = (r.total or 0) - shares.get(r.prompt_name, 0.0)
            print(
                f"{str(r.prompt_name):28} {r.n:>8} {r.total or 0:>16.6f} "
                f"{delta:>+10.6f}{flag}"
            )
        present = {r.prompt_name for r in rows}
        missing = labels - present
        if missing:
            print(f"  MISSING competitions this cycle: {sorted(missing)}")

        # --- recent score volume per (asset, time_length) ---
        print(
            f"\nminer_scores in the last {SCORES_LOOKBACK_HOURS}h "
            f"per (asset, time_length):"
        )
        print(f"{'asset':8} {'time_len':>8} {'#scores':>8} {'#miners':>8}")
        print("-" * 36)
        score_rows = conn.execute(
            text("""
                SELECT vr.asset, vr.time_length,
                       count(*) AS n,
                       count(DISTINCT mp.miner_id) AS miners
                FROM miner_scores ms
                JOIN miner_predictions mp ON mp.id = ms.miner_predictions_id
                JOIN validator_requests vr ON vr.id = mp.validator_requests_id
                WHERE ms.scored_time > now() - make_interval(
                    hours => :hours)
                GROUP BY vr.asset, vr.time_length
                ORDER BY vr.asset, vr.time_length
                """),
            {"hours": SCORES_LOOKBACK_HOURS},
        ).fetchall()
        for r in score_rows:
            print(
                f"{str(r.asset):8} {r.time_length:>8} {r.n:>8} {r.miners:>8}"
            )

        # --- recent on-chain weight sets ---
        print(f"\nlast {WEIGHTS_TO_SHOW} weights_update_history rows:")
        wh = conn.execute(
            text("""
                SELECT updated_at, update_result,
                       jsonb_array_length(norm_miner_weights::jsonb) AS n
                FROM weights_update_history
                ORDER BY updated_at DESC
                LIMIT :lim
                """),
            {"lim": WEIGHTS_TO_SHOW},
        ).fetchall()
        for r in wh:
            print(f"  {r.updated_at}  {r.update_result}  (n_uids={r.n})")


if __name__ == "__main__":
    main()

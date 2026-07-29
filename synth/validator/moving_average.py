from datetime import datetime
import typing


import numpy as np
import pandas as pd
from pandas import DataFrame
import bittensor as bt


from synth.utils.logging import print_execution_time
from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.competition_config import CompetitionConfig
from synth.validator.competition_config import SMOOTHED_SCORE_COEFFICIENT
from synth.validator.reward import compute_softmax

# Per-asset weighting coefficients for score normalization across assets.
# Higher coefficient = asset scores contribute more to the smoothed score.
ASSET_COEFFICIENTS = {
    "BTC": 1.0,
    "ETH": 0.7064366394033871,
    "XAU": 1.7370922597118699,
    "SOL": 0.6310037175639559,
    "SP500": 3.437935601155441,
    "NVDAX": 1.6028217601617174,
    "TSLAX": 1.6068755936957768,
    "AAPLX": 2.0916380815843123,
    "GOOGLX": 1.6827392777257926,
    "XRP": 0.5658394110809131,
    "HYPE": 0.4784547133706857,
    "WTIOIL": 0.8475062847978935,
    "SPCX": 0.4329342627683478,
}


@print_execution_time
def prepare_df_for_moving_average(df):
    """Prepare miner scores for moving average computation.

    For miners that joined after the earliest scored_time in the window,
    backfills missing earlier timestamps with the worst score (percentile95 - lowest_score)
    so they are not unfairly advantaged by having fewer data points.

    Miners present from the start keep only their real scores (no backfill).
    """
    df = df.copy()
    df["scored_time"] = pd.to_datetime(df["scored_time"])

    global_min = df["scored_time"].min()
    all_times = sorted(df["scored_time"].unique())

    # Build worst-score and asset mappings per scored_time (used for backfilling new miners)
    global_worst_score_mapping = {}
    global_score_asset_mapping = {}
    for t in all_times:
        sample = df.loc[df["scored_time"] == t].iloc[0]
        p95 = sample.get("percentile95")
        low = sample.get("lowest_score")
        if p95 is None or low is None:
            continue
        global_worst_score_mapping[t] = p95 - low
        global_score_asset_mapping[t] = sample["asset"]

    # Identify new miners (first appearance after the window start)
    miner_first = df.groupby("miner_id")["scored_time"].min()
    new_miner_ids = miner_first[miner_first > global_min].index

    if len(new_miner_ids) == 0:
        # No new miners - just return the real data (common fast path)
        out = df[
            ["scored_time", "miner_id", "prompt_score_v3", "asset"]
        ].copy()
        out["miner_id"] = out["miner_id"].astype(int)
        out = out.sort_values(["scored_time", "miner_id"]).reset_index(
            drop=True
        )
        return out

    # Build backfill rows only for new miners at times before they joined
    backfill_rows = []
    backfill_times = [t for t in all_times if t in global_worst_score_mapping]
    for mid in new_miner_ids:
        first_time = miner_first[mid]
        for t in backfill_times:
            if t >= first_time:
                continue
            backfill_rows.append(
                {
                    "scored_time": t,
                    "miner_id": mid,
                    "prompt_score_v3": global_worst_score_mapping[t],
                    "asset": global_score_asset_mapping[t],
                }
            )

    # Combine real data with backfill rows
    out = df[["scored_time", "miner_id", "prompt_score_v3", "asset"]].copy()
    if backfill_rows:
        backfill_df = pd.DataFrame(backfill_rows)
        backfill_df["scored_time"] = pd.to_datetime(backfill_df["scored_time"])
        out = pd.concat([out, backfill_df], ignore_index=True)

    out["miner_id"] = out["miner_id"].astype(int)
    out = out.sort_values(["scored_time", "miner_id"]).reset_index(drop=True)
    return out


@print_execution_time
def compute_smoothed_score(
    miner_data_handler: MinerDataHandler,
    input_df: DataFrame,
    scored_time: datetime,
    comp: CompetitionConfig,
) -> typing.Optional[list[dict]]:
    """Compute smoothed scores and reward weights for all miners.

    1. Filters scores to the scoring window (scored_time cutoff)
    2. Applies per-asset coefficient weighting (vectorized)
    3. Sums weighted scores per miner
    4. Assigns inf to miners with no valid scores
    5. Computes softmax reward weights
    """
    if input_df.empty:
        return None

    # Filter to scoring window and drop NaN scores
    df = input_df.loc[
        pd.to_datetime(input_df["scored_time"]) <= scored_time
    ].copy()
    df = df.dropna(subset=["prompt_score_v3"])

    if df.empty:
        return None

    # Apply per-asset coefficients vectorized (single pass, no per-miner loop).
    # Normalization is per-miner: each miner's scores are divided by the sum of
    # coefficients for that miner's assets.
    coefs = df["asset"].map(ASSET_COEFFICIENTS).fillna(1.0)
    df["weighted_score"] = df["prompt_score_v3"] * coefs
    miner_coef_sums = coefs.groupby(df["miner_id"]).sum()
    df["weighted_score"] /= df["miner_id"].map(miner_coef_sums)

    # Sum per miner in one groupby
    rolling_avgs = df.groupby("miner_id")["weighted_score"].sum()

    # Miners present in input but with all NaN scores get inf (worst possible)
    all_miner_ids = input_df["miner_id"].unique()
    missing_miners = set(all_miner_ids) - set(rolling_avgs.index)
    for miner_id in missing_miners:
        bt.logging.warning(
            f"Miner ID {miner_id} has no valid scores in the window. Assigning infinite rolling average."
        )
        rolling_avgs.loc[miner_id] = float("inf")

    rolling_avg_data = [
        {"miner_id": int(mid), "rolling_avg": float(val)}
        for mid, val in rolling_avgs.items()
    ]

    # Resolve miner_id -> miner_uid
    moving_averages_data = miner_data_handler.populate_miner_uid_in_miner_data(
        rolling_avg_data
    )

    if moving_averages_data is None:
        return None

    # Filter out miners with no UID (deregistered)
    filtered_moving_averages_data = [
        item for item in moving_averages_data if item["miner_uid"] is not None
    ]

    # Softmax: negative beta means lower score = higher reward
    rolling_avg_list = [
        r["rolling_avg"] for r in filtered_moving_averages_data
    ]
    reward_weight_list = compute_softmax(
        np.array(rolling_avg_list), comp.softmax_beta
    )

    rewards = []
    for item, reward_weight in zip(
        filtered_moving_averages_data, reward_weight_list
    ):
        if float(reward_weight) > 0:
            rewards.append(
                {
                    "miner_id": item["miner_id"],
                    "miner_uid": item["miner_uid"],
                    "smoothed_score": item["rolling_avg"],
                    "reward_weight": float(reward_weight)
                    * SMOOTHED_SCORE_COEFFICIENT,
                    "updated_at": scored_time.isoformat(),
                    "prompt_name": comp.label,
                }
            )

    return rewards


def print_rewards_df(moving_averages_data: list[dict], label: str = ""):
    bt.logging.info(f"Scored responses moving averages {label}")
    df = pd.DataFrame.from_dict(moving_averages_data)
    bt.logging.info(df.to_string())


@print_execution_time
def combine_moving_averages(
    moving_averages_data: dict[str, list[dict]],
) -> list[dict]:
    """Combine reward weights from competitions.

    Same miner appearing in multiple competitions gets their weights summed.
    """
    map_miner_reward: dict[int, dict] = {}

    for moving_averages in list(moving_averages_data.values()):
        for reward in moving_averages:
            miner_id = reward["miner_id"]
            if miner_id in map_miner_reward:
                map_miner_reward[miner_id]["reward_weight"] += reward[
                    "reward_weight"
                ]
            else:
                map_miner_reward[miner_id] = reward

    return list(map_miner_reward.values())

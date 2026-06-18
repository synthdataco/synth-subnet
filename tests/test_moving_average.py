import os
from datetime import datetime

import pandas as pd
from sqlalchemy import Engine

from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.moving_average import compute_smoothed_score
from synth.validator.competition_config import CRYPTO_24H


def read_csv(file_name):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, file_name)
    return pd.read_csv(file_path)


def test_moving_average_1(db_engine: Engine):
    handler = MinerDataHandler(db_engine)

    scored_time = datetime.fromisoformat("2025-02-21T17:23:00+00:00")

    df = read_csv("cutoff_data_4_days.csv")
    df["scored_time"] = pd.to_datetime(df["scored_time"])

    moving_averages_data = compute_smoothed_score(
        handler,
        input_df=df,
        scored_time=scored_time,
        comp=CRYPTO_24H,
    )

    # The miner id you want to search for
    target_id = 144

    assert moving_averages_data is not None

    # Select the element by miner id
    selected_miner = next(
        (
            item
            for item in moving_averages_data
            if item["miner_id"] == target_id
        ),
        None,
    )

    # Print the selected miner
    print("selected_miner", selected_miner)


def test_moving_average_2(db_engine: Engine):
    handler = MinerDataHandler(db_engine)

    scored_time = datetime.fromisoformat("2025-02-21T17:23:00+00:00")

    df = read_csv("cutoff_data_2_days.csv")
    df["scored_time"] = pd.to_datetime(df["scored_time"])

    moving_averages_data = compute_smoothed_score(
        handler,
        input_df=df,
        scored_time=scored_time,
        comp=CRYPTO_24H,
    )

    # The miner id you want to search for
    target_id = 144

    assert moving_averages_data is not None

    # Select the element by miner id
    selected_miner = next(
        (
            item
            for item in moving_averages_data
            if item["miner_id"] == target_id
        ),
        None,
    )

    print("selected_miner", selected_miner)

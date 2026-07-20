from datetime import datetime, timedelta, timezone
import logging
from unittest.mock import patch

import pytest

# from numpy.testing import assert_almost_equal
import bittensor as bt


from sqlalchemy import Engine, insert, select
from synth.miner.simulations import generate_simulations
from synth.simulation_input import SimulationInput
from synth.validator import response_validation_v2
from synth.validator.forward import (
    calculate_moving_average_and_update_rewards,
    calculate_scores,
)
from synth.db.models import (
    Miner,
    MinerPrediction,
    MinerReward,
    MinerScore,
    ValidatorRequest,
)
from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator import competition_config
from tests.utils import prepare_random_predictions, recent_start_time


def test_calculate_rewards_and_update_scores(db_engine: Engine):
    start_time = recent_start_time()
    scored_time = datetime.fromisoformat(start_time) + timedelta(
        hours=24, minutes=5
    )

    handler, _, miner_uids = prepare_random_predictions(db_engine, start_time)

    price_data_provider = PriceDataProvider()

    success = calculate_scores(
        miner_data_handler=handler,
        price_data_provider=price_data_provider,
        scored_time=scored_time,
        comp=competition_config.CRYPTO_24H,
    )

    assert success

    miner_scores_df = handler.get_miner_scores(
        scored_time,
        10,
        competition_config.CRYPTO_24H.time_length,
        competition_config.CRYPTO_24H.asset_list,
    )

    assert len(miner_scores_df) == len(miner_uids)

    print("miner_scores_df", miner_scores_df)


def test_calculate_moving_average_and_update_rewards(db_engine: Engine):
    start_time = recent_start_time()
    scored_time = datetime.fromisoformat(start_time) + timedelta(
        hours=24, minutes=5
    )

    handler, _, _ = prepare_random_predictions(db_engine, start_time)

    price_data_provider = PriceDataProvider()

    success = calculate_scores(
        miner_data_handler=handler,
        price_data_provider=price_data_provider,
        scored_time=scored_time,
        comp=competition_config.CRYPTO_24H,
    )

    assert success

    moving_averages_data = calculate_moving_average_and_update_rewards(
        miner_data_handler=handler,
        scored_time=scored_time,
    )

    print("moving_averages_data", moving_averages_data)


# Pin the miner's live-price fetch so these
# tests don't depend on it. PriceDataProvider's history fetch stays live — it
# hits the public Hyperliquid history endpoint and is part of what's scored.
@patch("synth.miner.simulations.get_asset_price", return_value=90000.0)
def test_calculate_moving_average_and_update_rewards_new_miner(
    mock_get_asset_price,
    db_engine: Engine,
):
    miner_uids = [10, 20, 33, 40, 50, 60]
    with db_engine.connect() as connection:
        with connection.begin():
            insert_stmt_validator = insert(Miner).values(
                [{"miner_uid": uid} for uid in miner_uids]
            )
            connection.execute(insert_stmt_validator)

    handler = MinerDataHandler(db_engine)
    # The loop rewrites `start_time_str` each iteration, so the effective
    # offset from base is 0,1,3,6,10,15h across 6 iterations. The latest
    # fetch window therefore ends at base + 15h + 24h = base + 39h, so the
    # base must be far enough back that this is safely in the past.
    start_time_str = recent_start_time(hours_ago=40)
    num_predictions = 6
    for i in range(num_predictions):
        miner_uids = [10, 20, 33, 40, 50, 60]
        start_time = datetime.fromisoformat(start_time_str).replace(
            tzinfo=timezone.utc
        ) + timedelta(hours=i)
        start_time_str = start_time.isoformat()
        simulation_input = SimulationInput(
            asset="HYPE",
            start_time=start_time_str,
            time_increment=300,
            time_length=86400,
            num_simulations=1,
        )

        simulation_data = {
            miner_uids[0]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "1.2",
            ),
            miner_uids[1]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "3",
            ),
            miner_uids[2]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "15",
            ),
            miner_uids[3]: (
                generate_simulations(start_time=start_time_str),
                "time out or internal server error (process time is None)",
                "2.1",
            ),
            miner_uids[4]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "1.5",
            ),
            miner_uids[5]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "5",
            ),
        }

        # simulate a miner that join later the subnet
        if i < 2:
            del simulation_data[miner_uids[5]]
            del miner_uids[5]

        handler.save_responses(
            simulation_data, simulation_input, datetime.now()
        )

        price_data_provider = PriceDataProvider()

        # scored time is start time + 24 hours and +4 minutes because new prompt every 64 minutes
        scored_time = start_time + timedelta(days=1, minutes=4)

        success = calculate_scores(
            miner_data_handler=handler,
            price_data_provider=price_data_provider,
            scored_time=scored_time,
            comp=competition_config.CRYPTO_24H,
        )

        miner_scores_df = handler.get_miner_scores(
            scored_time,
            10,
            competition_config.CRYPTO_24H.time_length,
            competition_config.CRYPTO_24H.asset_list,
        )

        print("miner_scores_df", miner_scores_df)

        assert success

        moving_averages_data = calculate_moving_average_and_update_rewards(
            miner_data_handler=handler,
            scored_time=scored_time,
        )

        print("moving_averages_data", moving_averages_data)


@patch("synth.miner.simulations.get_asset_price", return_value=90000.0)
def test_calculate_moving_average_and_update_rewards_new_miner_registration(
    mock_get_asset_price,
    db_engine: Engine,
):
    bt.logging._logger.setLevel(logging.DEBUG)
    miner_uids = [10, 20, 33, 40, 50, 60]
    with db_engine.connect() as connection:
        with connection.begin():
            records = []
            for uid in miner_uids:
                records.append(
                    {
                        "miner_uid": uid,
                        "coldkey": "5c" + str(uid),
                        "hotkey": "5h" + str(uid),
                    }
                )

            insert_stmt_validator = insert(Miner).values(records)
            connection.execute(insert_stmt_validator)

    handler = MinerDataHandler(db_engine)
    # See the sibling _new_miner test — cumulative offset reaches +15h,
    # so the fetch window ends at base + 39h. Anchor far enough back that
    # all 6 witness candles have settled.
    start_time_str = recent_start_time(hours_ago=40)
    num_predictions = 6
    for i in range(num_predictions):
        print("I is ", i)
        miner_uids = [10, 20, 33, 40, 50, 60]
        start_time = datetime.fromisoformat(start_time_str).replace(
            tzinfo=timezone.utc
        ) + timedelta(hours=i)
        start_time_str = start_time.isoformat()
        simulation_input = SimulationInput(
            asset="HYPE",
            start_time=start_time_str,
            time_increment=300,
            time_length=86400,
            num_simulations=1,
        )

        simulation_data = {
            miner_uids[0]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "1.2",
            ),
            miner_uids[1]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "3",
            ),
            miner_uids[2]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "15",
            ),
            miner_uids[3]: (
                generate_simulations(start_time=start_time_str),
                "time out or internal server error (process time is None)",
                "2.1",
            ),
            miner_uids[4]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "1.5",
            ),
            miner_uids[5]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "5",
            ),
        }

        # simulate a miner that join later the subnet
        if i < 2:
            del simulation_data[miner_uids[5]]
            del miner_uids[5]

        # simulate a new miner registration
        if i == 3:
            with db_engine.connect() as connection:
                with connection.begin():
                    insert_stmt_validator = insert(Miner).values(
                        [
                            {
                                "miner_uid": miner_uids[0],
                                "coldkey": "5cNew" + str(uid),
                                "hotkey": "5hNew" + str(uid),
                            }
                        ]
                    )
                    connection.execute(insert_stmt_validator)

        handler.save_responses(
            simulation_data, simulation_input, datetime.now()
        )

        price_data_provider = PriceDataProvider()

        # scored time is start time + 24 hours and +4 minutes because new prompt every 64 minutes
        scored_time = start_time + timedelta(days=1, minutes=4)

        success = calculate_scores(
            miner_data_handler=handler,
            price_data_provider=price_data_provider,
            scored_time=scored_time,
            comp=competition_config.CRYPTO_24H,
        )

        miner_scores_df = handler.get_miner_scores(
            scored_time,
            10,
            competition_config.CRYPTO_24H.time_length,
            competition_config.CRYPTO_24H.asset_list,
        )

        print("miner_scores_df: ", miner_scores_df)

        assert success

        moving_averages_data = calculate_moving_average_and_update_rewards(
            miner_data_handler=handler,
            scored_time=scored_time,
        )

        print("moving_averages_data", moving_averages_data)

        # sum the reward weights
        with db_engine.connect() as connection:
            with connection.begin():
                rewards_rows_select = select(MinerReward).where(
                    MinerReward.updated_at == scored_time
                )
                rewards_rows = connection.execute(rewards_rows_select).all()
                print("rewards_rows", rewards_rows)
                rewards_sum = sum([row.reward_weight for row in rewards_rows])
                print("rewards_sum", rewards_sum)

        miner_weights = [
            item["reward_weight"] for item in moving_averages_data
        ]
        print("sum miner_weights", sum(miner_weights))
        # assert_almost_equal(sum(miner_weights), 0.5, decimal=12)


def _seed_competition_scores(engine, comp, miner_ids, scores, scored_time):
    """Insert validator_request + miner_predictions + miner_scores for one
    competition's first asset, so calculate_moving_average_and_update_rewards
    can score it without any live price fetch."""
    now = datetime.now(timezone.utc)
    with engine.connect() as connection:
        with connection.begin():
            vr_id = connection.execute(
                insert(ValidatorRequest)
                .values(
                    start_time=scored_time
                    - timedelta(seconds=comp.time_length),
                    asset=comp.asset_list[0],
                    time_length=comp.time_length,
                    time_increment=comp.time_increment,
                    num_simulations=1,
                )
                .returning(ValidatorRequest.id)
            ).scalar_one()
            for miner_id, score in zip(miner_ids, scores):
                mp_id = connection.execute(
                    insert(MinerPrediction)
                    .values(
                        validator_requests_id=vr_id,
                        miner_uid=miner_id,
                        miner_id=miner_id,
                        prediction=[],
                        format_validation=response_validation_v2.CORRECT,
                        created_at=now,
                    )
                    .returning(MinerPrediction.id)
                ).scalar_one()
                connection.execute(
                    insert(MinerScore).values(
                        miner_uid=miner_id,
                        scored_time=scored_time,
                        miner_predictions_id=mp_id,
                        prompt_score=score,
                        prompt_score_v3=score,
                        score_details={},
                        score_details_v3={
                            "percentile95": 0.01,
                            "lowest_score": 0.0,
                        },
                    )
                )


def test_moving_average_writes_all_three_competitions(db_engine: Engine):
    """End-to-end (DB, no live prices): the moving-average update iterates
    ALL_COMPETITIONS, writes miner_rewards under each competition's label, and
    the combined weights sum to ~1.0 (3 competitions x SMOOTHED_SCORE_COEFFICIENT).
    """
    handler = MinerDataHandler(db_engine)
    miner_ids = [90101, 90102]  # high ids to avoid collision with other tests
    # 30 days back so the module-shared DB's rows from other tests (live
    # scores near now, with huge CRPS values) fall outside this cutoff and
    # can't drown the seeded miners in the softmax.
    scored_time = datetime.now(timezone.utc) - timedelta(days=30)

    now = datetime.now(timezone.utc)
    with db_engine.connect() as connection:
        with connection.begin():
            connection.execute(
                insert(Miner).values(
                    [
                        {
                            "id": m,
                            "miner_uid": m,
                            "created_at": now,
                            "updated_at": now,
                        }
                        for m in miner_ids
                    ]
                )
            )

    for comp in competition_config.ALL_COMPETITIONS:
        _seed_competition_scores(
            db_engine, comp, miner_ids, [0.002, 0.004], scored_time
        )

    combined = calculate_moving_average_and_update_rewards(
        miner_data_handler=handler,
        scored_time=scored_time,
    )

    # All three competition labels were written to miner_rewards for our miners.
    with db_engine.connect() as connection:
        labels = {
            row.prompt_name
            for row in connection.execute(
                select(MinerReward.prompt_name).where(
                    MinerReward.miner_id.in_(miner_ids)
                )
            ).fetchall()
        }
    assert labels == {c.label for c in competition_config.ALL_COMPETITIONS}

    # Each competition contributes SMOOTHED_SCORE_COEFFICIENT, so the combined
    # per-miner weights sum to ~1.0 across the three competitions.
    total = sum(item["reward_weight"] for item in combined)
    assert total == pytest.approx(1.0, abs=1e-6)


@patch("synth.miner.simulations.get_asset_price", return_value=90000.0)
def test_calculate_moving_average_and_update_rewards_only_invalid(
    mock_get_asset_price,
    db_engine: Engine,
):
    handler = MinerDataHandler(db_engine)
    # 3 iterations with the same cumulative-offset pattern; latest start
    # is base + 3h, so latest fetch ends at base + 27h.
    start_time_str = recent_start_time(hours_ago=28)

    handler.update_miner_rewards(
        [
            {
                "miner_uid": 0,
                "smoothed_score": float("nan"),
                "reward_weight": float("nan"),
                "updated_at": "2024-11-25T21:00:00+00:00",
            },
            {
                "miner_uid": 1,
                "smoothed_score": float("nan"),
                "reward_weight": float("nan"),
                "updated_at": "2024-11-25T21:00:00+00:00",
            },
            {
                "miner_uid": 2,
                "smoothed_score": float("nan"),
                "reward_weight": float("nan"),
                "updated_at": "2024-11-25T21:00:00+00:00",
            },
            {
                "miner_uid": 3,
                "smoothed_score": float("nan"),
                "reward_weight": float("nan"),
                "updated_at": "2024-11-25T21:00:00+00:00",
            },
        ]
    )

    num_predictions = 3
    for i in range(num_predictions):
        miner_uids = [0, 1, 2, 3, 4, 5]
        start_time = datetime.fromisoformat(start_time_str).replace(
            tzinfo=timezone.utc
        ) + timedelta(hours=i)
        start_time_str = start_time.isoformat()
        simulation_input = SimulationInput(
            asset="HYPE",
            start_time=start_time_str,
            time_increment=300,
            time_length=86400,
            num_simulations=1,
        )

        simulation_data = {
            miner_uids[0]: (
                [],
                "time out or internal server error (process time is None)",
                "1.2",
            ),
            miner_uids[1]: (
                [],
                "time out or internal server error (process time is None)",
                "3",
            ),
            miner_uids[2]: (
                generate_simulations(start_time=start_time_str),
                response_validation_v2.CORRECT,
                "15",
            ),
        }

        handler.save_responses(
            simulation_data, simulation_input, datetime.now()
        )

        price_data_provider = PriceDataProvider()

        # scored time is start time + 24 hours and +4 minutes because new prompt every 64 minutes
        scored_time = start_time + timedelta(days=1, minutes=4)

        success = calculate_scores(
            miner_data_handler=handler,
            price_data_provider=price_data_provider,
            scored_time=scored_time,
            comp=competition_config.CRYPTO_24H,
        )

        miner_scores_df = handler.get_miner_scores(
            scored_time,
            10,
            competition_config.CRYPTO_24H.time_length,
            competition_config.CRYPTO_24H.asset_list,
        )

        print("miner_scores_df", miner_scores_df)

        assert success

        moving_averages_data = calculate_moving_average_and_update_rewards(
            miner_data_handler=handler,
            scored_time=scored_time,
        )

        print("moving_averages_data", moving_averages_data)

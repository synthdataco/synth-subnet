from datetime import datetime, timedelta, timezone
from unittest.mock import patch


from sqlalchemy import Engine, insert


from synth.miner.simulations import generate_simulations
from synth.simulation_input import SimulationInput
from synth.validator import response_validation_v2
from synth.validator.miner_data_handler import MinerDataHandler
from synth.db.models import Miner


def recent_start_time(hours_ago: int = 25) -> str:
    """Return an ISO start_time `hours_ago` hours before now (minute-aligned).

    Live tests against the Pyth Pro Router can only query its rolling
    history window, so hardcoded dates from months ago return empty data
    and trip the settlement guard. Tests that exercise the full scoring
    path call this to pick a window the router still serves.
    """
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return (now - timedelta(hours=hours_ago)).isoformat()


def generate_values(start_time: datetime):
    """Generate values for the given start_time."""
    values = []
    for i in range(0, 24 * 60, 5):  # 5-minute intervals for 1 day
        time_point = (start_time + timedelta(minutes=i)).isoformat()
        price = 90000 + i * 100  # Random price
        values.append({"time": time_point, "price": price})

    return [values]


def prepare_random_predictions(db_engine: Engine, start_time: str):
    handler = MinerDataHandler(db_engine)
    miner_uids = [0, 1, 2, 3]

    with db_engine.connect() as connection:
        with connection.begin():
            insert_stmt_validator = insert(Miner).values(
                [{"miner_uid": uid} for uid in miner_uids]
            )
            connection.execute(insert_stmt_validator)

    simulation_input = SimulationInput(
        asset="BTC",
        start_time=start_time,
        time_increment=300,
        time_length=86400,
        num_simulations=1,
    )

    # generate_simulations() fetches a live current price via get_asset_price
    # Tests must not depend on that external call, so pin the price and let the GBM math run on it.
    with patch(
        "synth.miner.simulations.get_asset_price", return_value=90000.0
    ):
        simulation_data = {
            miner_uids[0]: (
                generate_simulations(start_time=start_time),
                response_validation_v2.CORRECT,
                "1.2",
            ),
            miner_uids[1]: (
                generate_simulations(start_time=start_time),
                response_validation_v2.CORRECT,
                "3",
            ),
            miner_uids[2]: (
                generate_simulations(start_time=start_time),
                response_validation_v2.CORRECT,
                "15",
            ),
            miner_uids[3]: (
                generate_simulations(start_time=start_time),
                "time out or internal server error (process time is None)",
                "2.1",
            ),
        }
    handler.save_responses(simulation_data, simulation_input, datetime.now())

    return handler, simulation_input, miner_uids

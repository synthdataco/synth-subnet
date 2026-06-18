from unittest.mock import patch

from synth.miner.simulations import generate_simulations
from synth.simulation_input import SimulationInput
from synth.utils.helpers import get_current_time, round_time_to_minutes
from synth.validator.response_validation_v2 import CORRECT, validate_responses


# get_asset_price hits a live price feed;
# pin it so these tests exercise the simulation math without a network call.
@patch("synth.miner.simulations.get_asset_price", return_value=90000.0)
def test_generate_simulations(mock_get_asset_price):
    result = generate_simulations(
        asset="BTC",
        start_time="2025-02-04T00:00:00+00:00",
        time_increment=300,
        time_length=86400,
        num_simulations=100,
    )

    assert isinstance(result, tuple)
    assert len(result) == 102
    assert all(
        isinstance(sub_array, list) and len(sub_array) == 289
        for sub_array in result[2:]
    )


@patch("synth.miner.simulations.get_asset_price", return_value=90000.0)
def test_run(mock_get_asset_price):
    simulation_input = SimulationInput(
        asset="BTC",
        time_increment=300,
        time_length=86400,
        num_simulations=100,
    )

    current_time = get_current_time()
    start_time = round_time_to_minutes(current_time, 120)
    simulation_input.start_time = start_time.isoformat()

    print("start_time", simulation_input.start_time)
    prediction = generate_simulations(
        simulation_input.asset,
        start_time=simulation_input.start_time,
        time_increment=simulation_input.time_increment,
        time_length=simulation_input.time_length,
        num_simulations=simulation_input.num_simulations,
    )

    format_validation = validate_responses(
        prediction,
        simulation_input,
        "0",
    )

    print(format_validation)

    assert format_validation == CORRECT

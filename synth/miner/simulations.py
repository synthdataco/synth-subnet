from synth.miner.price_simulation import (
    simulate_crypto_price_paths,
    get_asset_price,
)
from synth.utils.helpers import (
    convert_prices_to_time_format,
)

SIGMA_MAP = {
    "BTC": 0.00541,
    "ETH": 0.00766,
    "XAU": 0.00312,
    "SOL": 0.00858,
    # SP500 inherits SPYX's sigma (index vol == tokenized-SPY vol).
    # SPYX stays for the rollout tail — remove with the Pyth code path.
    "SP500": 0.00157,
    "SPYX": 0.00157,
    "NVDAX": 0.00338,
    "TSLAX": 0.00337,
    "AAPLX": 0.00259,
    "GOOGLX": 0.00322,
    "XRP": 0.00956,
    "HYPE": 0.01131,
    "WTIOIL": 0.00639,
}


def generate_simulations(
    asset="BTC",
    start_time: str = "",
    time_increment=300,
    time_length=86400,
    num_simulations=1,
):
    """
    Generate simulated price paths.

    Parameters:
        asset (str): The asset to simulate. Default is 'BTC'.
        start_time (str): The start time of the simulation. Defaults to current time.
        time_increment (int): Time increment in seconds.
        time_length (int): Total time length in seconds.
        num_simulations (int): Number of simulation runs.

    Returns:
        numpy.ndarray: Simulated price paths.
    """
    if start_time == "":
        raise ValueError("Start time must be provided.")

    current_price = get_asset_price(asset)
    if current_price is None:
        raise ValueError(f"Failed to fetch current price for asset: {asset}")

    sigma = SIGMA_MAP.get(asset, 0.005)  # Default sigma if asset not found

    simulations = simulate_crypto_price_paths(
        current_price=current_price,
        time_increment=time_increment,
        time_length=time_length,
        num_simulations=num_simulations,
        sigma=sigma,
    )

    predictions = convert_prices_to_time_format(
        simulations.tolist(), start_time, time_increment
    )

    return predictions

import numpy as np
from properscoring import crps_ensemble


def get_interval_steps(scoring_interval: int, time_increment: int) -> int:
    """
    Calculate the number of steps in the given scoring interval based on the time increment.
    """
    return int(scoring_interval / time_increment)


def calculate_crps_for_miner(
    simulation_runs: np.ndarray,
    real_price_path: np.ndarray,
    time_increment: int,
    scoring_intervals: dict[str, int],
) -> tuple[float, list[dict]]:
    """
    Calculate the total CRPS score for a miner's simulations over specified intervals,
    Return the sum of the scores.

    Parameters:
        simulation_runs (numpy.ndarray): Simulated price paths.
        real_price_path (numpy.ndarray): The real price path.
        time_increment (int): Time increment in seconds.
        scoring_intervals (dict): Dictionary of scoring intervals with their names and durations in seconds.

    Returns:
        float: Sum of total CRPS scores over the intervals.
    """
    # Initialize lists to store detailed CRPS data
    detailed_crps_data: list[dict] = []
    sum_all_scores = 0.0
    gap_total_crps = 0.0

    for interval_name, interval_seconds in scoring_intervals.items():
        interval_steps = get_interval_steps(interval_seconds, time_increment)
        absolute_price = interval_name.endswith("_abs")
        is_gap = interval_name.endswith("_gaps")

        # If we are considering absolute prices, adjust the interval steps for potential gaps:
        # if only the initial price is present, then decrease the interval step
        if absolute_price:
            while (
                real_price_path[::interval_steps].shape[0] == 1
                and interval_steps > 1
            ):
                interval_steps -= 1

        # Make sure there are no zero prices in the simulation runs because it will cause a division by zero error
        if np.any(simulation_runs == 0):
            return -1.0, [
                {"error": "Zero price encountered in simulation runs"}
            ]

        # Calculate price changes over intervals
        simulated_changes = calculate_price_changes_over_intervals(
            simulation_runs,
            interval_steps,
            absolute_price,
            is_gap,
        )
        real_changes = calculate_price_changes_over_intervals(
            real_price_path.reshape(1, -1),
            interval_steps,
            absolute_price,
            is_gap,
        )
        data_blocks = label_observed_blocks(real_changes[0])

        # Not enough observed data -> continue
        if len(data_blocks) == 0:
            continue

        # Calculate CRPS over intervals
        crps_values = sum_crps_over_blocks(
            simulated_changes,
            real_changes,
            data_blocks,
            absolute_price,
            real_price_path,
        )

        # Total CRPS for this interval
        total_crps_interval = float(crps_values)
        sum_all_scores += total_crps_interval
        if is_gap:
            # Individual *_gaps intervals are folded into the single "Gaps"
            # Total appended below
            gap_total_crps += total_crps_interval
            continue

        detailed_crps_data.append(
            {
                "Interval": interval_name,
                "Increment": "Total",
                "CRPS": total_crps_interval,
            }
        )

    if gap_total_crps > 0:
        detailed_crps_data.append(
            {"Interval": "Gaps", "Increment": "Total", "CRPS": gap_total_crps}
        )

    detailed_crps_data.append(
        {"Interval": "Overall", "Increment": "Total", "CRPS": sum_all_scores}
    )

    return sum_all_scores, detailed_crps_data


def calculate_vol_crps_for_miner(
    simulation_runs: np.ndarray,
    real_price_path: np.ndarray,
    time_increment: int,
    vol_scoring_blocks: dict[str, tuple[int, float]],
) -> tuple[float, list[dict]]:
    """
    Calculate the weighted volatility CRPS for a miner's simulations.

    The path is cut into consecutive blocks of each configured size, the
    realized volatility of every block is scored against the ensemble of
    simulated block volatilities, and the per-block CRPS values are summed
    and scaled by that block size's weight.

    Parameters:
        simulation_runs (numpy.ndarray): Simulated price paths.
        real_price_path (numpy.ndarray): The real price path.
        time_increment (int): Time increment in seconds.
        vol_scoring_blocks (dict): Dictionary of block sizes with their names,
            durations in seconds and weights.

    Returns:
        float: Sum of the weighted volatility CRPS over the block sizes.
    """
    detailed_crps_data: list[dict] = []
    sum_all_scores = 0.0

    for name, (block_seconds, weight) in vol_scoring_blocks.items():
        block_steps = get_interval_steps(block_seconds, time_increment)

        simulated_vol = block_volatilities(simulation_runs, block_steps)
        real_vol = block_volatilities(
            real_price_path.reshape(1, -1), block_steps
        )[0]
        observed_blocks = np.flatnonzero(~np.isnan(real_vol))

        # Not enough observed data -> continue
        if observed_blocks.size == 0:
            continue

        crps_sum = float(
            sum(
                crps_ensemble(real_vol[block], simulated_vol[:, block])
                for block in observed_blocks
            )
        )
        sum_all_scores += weight * crps_sum

        detailed_crps_data.append(
            {
                "Interval": name,
                "Increment": "Total",
                "CRPS": crps_sum,
                "Weight": weight,
            }
        )

    detailed_crps_data.append(
        {"Interval": "Vol", "Increment": "Total", "CRPS": sum_all_scores}
    )

    return sum_all_scores, detailed_crps_data


def calculate_total_score_for_miner(
    simulation_runs: np.ndarray,
    real_price_path: np.ndarray,
    time_increment: int,
    scoring_intervals: dict[str, int],
    vol_scoring_blocks: dict[str, tuple[int, float]],
) -> tuple[float, list[dict]]:
    """
    Calculate a miner's total score: the price CRPS plus the weighted
    volatility CRPS. A competition with no volatility blocks scores on the
    price CRPS alone.

    Returns:
        float: The total score.
    """
    price_score, detailed_crps_data = calculate_crps_for_miner(
        simulation_runs, real_price_path, time_increment, scoring_intervals
    )

    # -1 is the rejection sentinel of the price pass, which also owns the
    # zero-price guard the volatility blocks depend on: never add to it.
    if not vol_scoring_blocks or price_score == -1.0:
        return price_score, detailed_crps_data

    vol_score, detailed_vol_data = calculate_vol_crps_for_miner(
        simulation_runs, real_price_path, time_increment, vol_scoring_blocks
    )
    total_score = price_score + vol_score

    # "Overall" holds the score actually returned and stays the last row.
    detailed_data = [
        row for row in detailed_crps_data if row["Interval"] != "Overall"
    ]
    detailed_data += detailed_vol_data
    detailed_data.append(
        {"Interval": "Overall", "Increment": "Total", "CRPS": total_score}
    )

    return total_score, detailed_data


def sum_crps_over_blocks(
    simulated_changes: np.ndarray,
    real_changes: np.ndarray,
    data_blocks: np.ndarray,
    absolute_price: bool,
    real_price_path: np.ndarray,
) -> float:
    """
    Sum the CRPS over the observed (non-missing) blocks of a single interval.
    """
    crps_values = 0.0
    for block in np.unique(data_blocks):
        # skip missing value blocks
        if block == -1:
            continue

        mask = data_blocks == block
        simulated_changes_block = simulated_changes[:, mask]
        real_changes_block = real_changes[0, mask]  # 1D array now
        num_intervals = simulated_changes_block.shape[1]

        # Calculate all CRPS values at once
        crps_values_block = np.array(
            [
                crps_ensemble(
                    real_changes_block[t], simulated_changes_block[:, t]
                )
                for t in range(num_intervals)
            ]
        )

        if absolute_price:
            crps_values_block = (
                crps_values_block / real_price_path[-1] * 10_000
            )

        crps_values += float(crps_values_block.sum())

    return float(crps_values)


def label_observed_blocks(arr: np.ndarray) -> np.ndarray:
    """
    Groups blocks of consecutive observed data together.
    Example input/output:
    [1.0, 2.0, np.nan, 4.0, np.nan, np.nan, 7.0, 8.0] -> [0, 0, -1, 1, -1, -1, 2, 2]
    """
    not_nan = ~np.isnan(arr)
    block_start = not_nan & np.concatenate(([True], ~not_nan[:-1]))
    group_numbers = np.cumsum(block_start) - 1
    group_labels = np.where(not_nan, group_numbers, -1)
    return group_labels


def calculate_price_changes_over_intervals(
    price_paths: np.ndarray,
    interval_steps: int,
    absolute_price=False,
    is_gap=False,
) -> np.ndarray:
    """
    Calculate price changes over specified intervals.

    Parameters:
        price_paths (numpy.ndarray): Array of simulated price paths.
        interval_steps (int): Number of steps that make up the interval.
        absolute_price (bool): If True, absolute price values (rather than price changes) are returned.

    Returns:
        numpy.ndarray: Array of price changes over intervals.
    """
    # Get the prices at the interval points
    # [1, 2, 3, 4, 5, 6, 7] -> [1, 3, 5, 7] if interval_steps is 2
    interval_prices = price_paths[:, ::interval_steps]
    if is_gap:
        # [1, 2, 3, 4, 5, 6, 7] -> [1, 3] if interval_steps is 2
        interval_prices = interval_prices[:, :2]

    # Calculate price changes over intervals
    if absolute_price:
        return interval_prices[:, 1:]

    return (
        np.diff(interval_prices, axis=1) / interval_prices[:, :-1]
    ) * 10_000


def block_volatilities(
    price_paths: np.ndarray, block_steps: int
) -> np.ndarray:
    """
    Calculate the volatility of each consecutive block of a price path.

    Parameters:
        price_paths (numpy.ndarray): Array of price paths.
        block_steps (int): Number of steps that make up a block.

    Returns:
        numpy.ndarray: Standard deviation, in basis points, of the step
        returns inside each block. A block with fewer than two observed
        returns is NaN so that it can be skipped; the steps left over after
        the last whole block are dropped.
    """
    returns = (np.diff(price_paths, axis=1) / price_paths[:, :-1]) * 10_000

    n_blocks = returns.shape[1] // block_steps
    blocks = returns[:, : n_blocks * block_steps].reshape(
        returns.shape[0], n_blocks, block_steps
    )

    # Only the scorable blocks go through nanstd: it warns on a block with
    # fewer than two observed returns, which is the tolerated case here.
    scorable = np.sum(~np.isnan(blocks), axis=2) >= 2
    volatilities: np.ndarray = np.full(scorable.shape, np.nan)
    volatilities[scorable] = np.nanstd(blocks[scorable], axis=1, ddof=1)

    return volatilities

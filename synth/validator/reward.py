# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Set your name
# Copyright © 2023 <your name>

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

import typing
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import shared_memory
import time

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
import numpy as np
import pandas as pd
import bittensor as bt


from synth.db.models import MinerPrediction, ValidatorRequest
from synth.utils.helpers import adjust_predictions
from synth.utils.logging import print_execution_time
from synth.validator.crps_calculation import calculate_crps_for_miner
from synth.validator.miner_data_handler import MinerDataHandler
from synth.validator.price_data_provider import PriceDataProvider
from synth.validator import response_validation_v2
from synth.validator import competition_config


# Module level - must be picklable
def _crps_worker(args):
    """Standalone worker - no database, no complex objects.
    Uses shared memory for real_prices to reduce memory duplication across processes.
    """
    (
        miner_uid,
        prediction_array,
        shm_name,
        prices_shape,
        time_increment,
        scoring_intervals,
        format_validation,
        prediction_id,
        process_time,
    ) = args

    # Early returns
    if prediction_array is None:
        return (
            miner_uid,
            -1,
            [],
            None,
            format_validation,
            prediction_id,
            process_time,
        )

    if format_validation != "CORRECT":  # Use string, not enum
        return (
            miner_uid,
            -1,
            [],
            None,
            format_validation,
            prediction_id,
            process_time,
        )

    if prices_shape[0] == 0:
        return (
            miner_uid,
            -1,
            [],
            None,
            format_validation,
            prediction_id,
            process_time,
        )

    # Attach to shared memory for real_prices
    existing_shm = shared_memory.SharedMemory(name=shm_name)
    try:
        real_prices = np.ndarray(
            prices_shape, dtype=np.float64, buffer=existing_shm.buf
        )

        prediction_array = adjust_predictions(list(prediction_array))

        try:
            simulation_runs = np.array(prediction_array).astype(float)
            score, detailed_crps_data = calculate_crps_for_miner(
                simulation_runs,
                real_prices,  # Already a numpy array from shared memory
                int(time_increment),
                scoring_intervals,
            )

            if not np.isfinite(score):
                # A non-finite total means at least one interval is non-finite
                # too, so the detailed data goes with the score: JSONB has no
                # NaN/Infinity, and score_details_v3 is written for every
                # miner in one INSERT, so one such row fails the whole prompt.
                return (
                    miner_uid,
                    -1,
                    [],
                    f"Error calculating CRPS for miner {miner_uid}: "
                    f"non-finite score {score}",
                    format_validation,
                    prediction_id,
                    process_time,
                )

            return (
                miner_uid,
                score,
                detailed_crps_data,
                None,
                format_validation,
                prediction_id,
                process_time,
            )

        except Exception as e:
            return (
                miner_uid,
                -1,
                [],
                str(e),
                format_validation,
                prediction_id,
                process_time,
            )
    finally:
        existing_shm.close()


# Global executor - create once
_PROCESS_EXECUTOR = None


def get_process_executor(nprocs: int = 2) -> ProcessPoolExecutor:
    global _PROCESS_EXECUTOR
    if _PROCESS_EXECUTOR is None:
        _PROCESS_EXECUTOR = ProcessPoolExecutor(max_workers=nprocs)
    return _PROCESS_EXECUTOR


def _prepare_work_items(
    predictions: list[MinerPrediction],
    shm_name: str,
    prices_shape: tuple,
    validator_request: ValidatorRequest,
    scoring_intervals: dict,
) -> list[tuple]:
    """Prepare picklable work items for multiprocess CRPS calculation."""
    work_items = []

    for pred in predictions:
        # Convert to picklable types
        format_val = pred.format_validation
        # Convert enum to string if needed
        if hasattr(format_val, "value"):
            format_val = format_val.value
        elif format_val == response_validation_v2.CORRECT:
            format_val = "CORRECT"
        else:
            format_val = str(format_val)

        work_items.append(
            (
                pred.miner_uid,
                list(pred.prediction),
                shm_name,
                prices_shape,
                int(validator_request.time_increment),
                scoring_intervals,
                format_val,
                int(pred.id),
                (
                    float(pred.process_time)
                    if pred.process_time is not None
                    else 0.0
                ),
            )
        )

    return work_items


def _build_detailed_info(
    predictions: list[MinerPrediction],
    scores: list,
    detailed_crps_data_list: list,
    prompt_scores: np.ndarray,
    miner_prediction_format_list: list,
    miner_prediction_id_list: list,
    miner_prediction_process_time: list,
    percentile95: float,
    lowest_score: float,
    was_capped: np.ndarray,
) -> list[dict]:
    """Build detailed information dict from processing results.

    score_capped marks a response clipped to the outlier ceiling. It is what
    makes the cap monitorable after the fact — a rising rate is the signal that
    someone is provoking it to inflate the field rather than merely predicting
    badly.
    """
    return [
        {
            "miner_uid": pred.miner_uid,
            "prompt_score_v3": float(prompt_score),
            "percentile95": float(percentile95),
            "lowest_score": float(lowest_score),
            "score_capped": bool(capped),
            "miner_prediction_id": prediction_id,
            "format_validation": format,
            "process_time": process_time,
            "total_crps": float(score),
            "crps_data": clean_numpy_in_crps_data(crps_data),
        }
        for (
            pred,
            score,
            crps_data,
            prompt_score,
            format,
            prediction_id,
            process_time,
            capped,
        ) in zip(
            predictions,
            scores,
            detailed_crps_data_list,
            prompt_scores,
            miner_prediction_format_list,
            miner_prediction_id_list,
            miner_prediction_process_time,
            was_capped,
        )
    ]


@print_execution_time
def get_rewards_multiprocess(
    miner_data_handler: MinerDataHandler,
    price_data_provider: PriceDataProvider,
    validator_request: ValidatorRequest,
    comp: competition_config.CompetitionConfig,
    nprocs: int = 2,
) -> tuple[typing.Optional[np.ndarray], list, list[dict]]:
    """
    Returns an array of rewards for the given query and responses.
    Uses shared memory for real_prices to reduce memory duplication across worker processes.

    Args:
    - miner_data_handler (MinerDataHandler): The handler for miner data.
    - price_data_provider (PriceDataProvider): The provider for price data.
    - validator_request (ValidatorRequest): The validator request object.
    - comp (CompetitionConfig): The competition being scored; supplies the scoring intervals used for CRPS.
    - nprocs (int): Number of processes to use for parallel computation.

    Returns:
    - np.ndarray: An array of rewards for the given query and responses.
    - list: Detailed information for each miner.
    - list[dict]: The real prices used for calculation.
    """
    try:
        real_prices = price_data_provider.fetch_data(validator_request)
    except Exception as e:
        bt.logging.warning(f"Error fetching data: {e}")
        return None, [], []

    if real_prices is None or len(real_prices) == 0:
        bt.logging.warning(
            f"No price data for {validator_request.asset} "
            f"(start_time={validator_request.start_time}). Skipping."
        )
        return None, [], []

    predictions = miner_data_handler.get_predictions_by_request(
        validator_request
    )

    if predictions is None or len(predictions) == 0:
        bt.logging.warning(
            f"No predictions for request {validator_request.id}. Skipping."
        )
        return None, [], []

    # Create shared memory for real_prices to avoid duplicating across workers
    prices_array = np.array(real_prices, dtype=np.float64)
    shm = shared_memory.SharedMemory(create=True, size=prices_array.nbytes)
    shared_prices = np.ndarray(
        prices_array.shape, dtype=np.float64, buffer=shm.buf
    )
    shared_prices[:] = prices_array[:]

    # Prepare work items
    work_items = _prepare_work_items(
        predictions,
        shm.name,
        prices_array.shape,
        validator_request,
        comp.scoring_intervals,
    )

    # Process in parallel (CPU bound - use ProcessPool)
    bt.logging.info(f"Starting CRPS calculation for {len(work_items)} miners")
    t0 = time.time()

    try:
        executor = get_process_executor(nprocs)
        results = list(executor.map(_crps_worker, work_items))
    finally:
        # Clean up shared memory
        shm.close()
        shm.unlink()

    bt.logging.info(f"CRPS done in {time.time() - t0:.2f}s")

    # Rebuild results
    scores = []
    detailed_crps_data_list = []
    miner_prediction_format_list = []
    miner_prediction_id_list = []
    miner_prediction_process_time = []

    for (
        miner_uid,
        score,
        detailed_crps_data,
        error,
        format_val,
        prediction_id,
        process_time,
    ) in results:
        if error:
            bt.logging.error(f"Miner {miner_uid} error: {error}")

        scores.append(score)
        detailed_crps_data_list.append(detailed_crps_data)
        miner_prediction_format_list.append(format_val)
        miner_prediction_id_list.append(prediction_id)
        miner_prediction_process_time.append(process_time)

    score_values = np.array(scores)
    prompt_scores, percentile95, lowest_score, was_capped = (
        compute_prompt_scores(score_values)
    )

    if prompt_scores is None:
        bt.logging.warning(
            f"All predictions invalid for request {validator_request.id}. Skipping."
        )
        return None, [], []

    detailed_info = _build_detailed_info(
        predictions,
        scores,
        detailed_crps_data_list,
        prompt_scores,
        miner_prediction_format_list,
        miner_prediction_id_list,
        miner_prediction_process_time,
        percentile95,
        lowest_score,
        was_capped,
    )

    return prompt_scores, detailed_info, real_prices


# Ceiling on a single miner's CRPS, as a multiple of the field's MEDIAN.
#
# Response validation only rejects prices above the float32 ceiling (~3.4e38),
# so a forecast of 1e30 is accepted as CORRECT and produces a CRPS of the same
# order. Uncapped, one such response does two kinds of damage:
#
#   1. It lands in the p95 that fills MISSED responses, so a miner that merely
#      timed out inherits an astronomical score it had no part in producing.
#   2. It survives into the moving average, where exp(beta * score) underflows
#      to exactly 0.0 — and compute_smoothed_score drops zero-weight miners
#      entirely, so the miner vanishes from the results rather than ranking last.
#
# The multiple was chosen against real scoring history: responses beyond 10x
# their prompt's median are vanishingly rare and, where they occur, are already
# unusable. Real predictions do not spread that far, so this never touches
# legitimate scoring and does not change a miner's optimum strategy.
#
# A much larger multiple was tried first and rejected. It leaves the capped
# value orders of magnitude above a normal score, which distorts dashboards and,
# for a miner capped on several prompts, pushes the windowed mean back toward
# the underflow-to-zero behaviour this exists to prevent. 10x keeps a capped row
# on the same scale as the rest of the field.
#
# This is NOT the p90 cap removed in #302: that compressed ordinary scores,
# whereas at 10x nothing ordinary is touched at all.
#
# The median is the right basis because it is unmoved by a large minority of
# garbage — the p95 is precisely what garbage captures first.
OUTLIER_SCORE_MEDIAN_MULTIPLE = 10.0


@print_execution_time
def compute_prompt_scores(score_values: np.ndarray):
    """Returns (prompt_scores, percentile95, lowest_score, was_capped).

    was_capped is a bool array aligned with score_values, True where a response
    was clipped to the outlier ceiling. It is persisted per row so the cap is
    monitorable — if the rate ever climbs, someone may be provoking it
    deliberately to inflate the field.
    """
    if np.all(score_values == -1):
        return None, 0, 0, None
    score_values = np.asarray(score_values, dtype=float)
    score_values_valid = score_values[score_values != -1]

    # Clip absurd outliers BEFORE deriving anything from them. The cap is taken
    # on the RAW crps, before the best score is subtracted: raw crps has a
    # stable positive scale set by the asset's price, whereas post-subtraction
    # scores start at 0 and a tightly-bunched field would give a median near
    # zero — collapsing a median-multiple cap onto legitimate scores.
    median = float(np.median(score_values_valid))
    # Aligned with score_values, so it can be persisted per row. Misses (-1) are
    # below any positive cap and so are never flagged.
    was_capped = np.zeros(score_values.shape, dtype=bool)
    if median > 0:
        cap = median * OUTLIER_SCORE_MEDIAN_MULTIPLE
        was_capped = score_values > cap
        if was_capped.any():
            bt.logging.warning(
                f"{int(was_capped.sum())} of {score_values_valid.size} valid "
                f"response(s) exceeded {OUTLIER_SCORE_MEDIAN_MULTIPLE:g}x the "
                f"median CRPS ({median:.4g}); clipping to {cap:.4g} and "
                f"excluding them from the p95 used to fill missed responses."
            )
        # cap > 0, so np.minimum leaves the -1 miss sentinels untouched.
        score_values = np.minimum(score_values, cap)

    # The p95 that fills MISSED responses is taken over the scores that were NOT
    # capped. Clipping alone is not enough: once more than 5% of the field is
    # garbage the p95 lands on the cap itself, so a miner that merely timed out
    # still inherits a near-fatal score it had no part in producing. Excluding
    # capped scores keeps the two cases properly separated — submit garbage and
    # you are capped and ranked last; miss the prompt and you are scored like
    # the 95th percentile of the miners who actually answered.
    uncapped = score_values[(score_values != -1) & ~was_capped]
    percentile95 = np.percentile(
        uncapped if uncapped.size else score_values[score_values != -1], 95
    )
    # Valid scores are capped (above) but not otherwise compressed; only missed
    # responses (-1) are filled.
    filled_scores = np.where(score_values == -1, percentile95, score_values)
    lowest_score = np.min(filled_scores)
    return filled_scores - lowest_score, percentile95, lowest_score, was_capped


def compute_softmax(score_values: np.ndarray, beta: float) -> np.ndarray:
    bt.logging.info(f"Going to use the following value of beta: {beta}")

    if len(score_values) == 0:
        return np.array([])

    scaled_scores = beta * score_values
    scaled_scores -= np.max(scaled_scores)
    exp_scores = np.exp(scaled_scores)
    softmax_scores_valid: np.ndarray = exp_scores / np.sum(exp_scores)
    return softmax_scores_valid


def clean_numpy_in_crps_data(crps_data: list) -> list:
    cleaned_crps_data = [
        {
            key: (float(value) if isinstance(value, np.float64) else value)
            for key, value in item.items()
        }
        for item in crps_data
    ]
    return cleaned_crps_data


def print_scores_df(prompt_scores, detailed_info):
    bt.logging.info(f"Scored responses: {prompt_scores}")

    df = pd.DataFrame.from_dict(detailed_info)
    if df.empty:
        bt.logging.info("No data to display.")
        return
    # Drop columns that are not needed for logging
    if "crps_data" in df.columns:
        df = df.drop(columns=["crps_data"])
    if "real_prices" in df.columns:
        df = df.drop(columns=["real_prices"])
    bt.logging.info(df.to_string())

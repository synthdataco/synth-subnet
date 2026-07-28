from datetime import datetime
import math
import struct
import typing

import numpy as np

from synth.simulation_input import SimulationInput

CORRECT = "CORRECT"

MAX_SIGNIFICANT_DIGITS = 8


def _point_error(point) -> typing.Optional[str]:
    """The rule, per point: an int or float (bools excluded), finite,
    representable as float32, with at most MAX_SIGNIFICANT_DIGITS significant
    digits — i.e. the value round-trips through its 8-significant-digit
    decimal form."""
    if isinstance(point, bool) or not isinstance(point, (int, float)):
        return f"Price format is incorrect: expected int or float, got {type(point)}"

    try:
        value = float(point)
    except OverflowError:  # int beyond float64 range
        return f"Price format is incorrect: too many digits {point}"

    if not math.isfinite(value):
        return f"Price format is incorrect: non-finite value {point}"

    # Predictions are stored as float32: out of range reads back as inf, too
    # small collapses to 0.
    try:
        as_float32 = struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return f"Price format is incorrect: exceeds float32 range {point}"

    if as_float32 == 0.0 and value != 0.0:
        return f"Price format is incorrect: underflows float32 {point}"

    if float(f"{value:.{MAX_SIGNIFICANT_DIGITS - 1}e}") != value:
        return f"Price format is incorrect: too many digits {point}"

    return None


def _sig_digits_pass_mask(matrix: np.ndarray) -> np.ndarray:
    """Vectorised _point_error: True marks points proven valid; False only
    means "recheck with the exact scalar rule".

    A point is proven by scaling it to an integer mantissa of at most
    8 digits and checking the scaling round-trips exactly. Only positive
    powers of ten are used (negative ones are not exact in float64), so
    the equality is exact — and the [1e-14, 1e14] band plus the mantissa
    cap keep it exact even if log10 is off by one at a decade boundary.

    The band sits inside float32 range, so proven points need no float32
    check; widening it means revisiting that.
    """
    if matrix.dtype.kind != "f":
        matrix = matrix.astype(np.float64)

    absx = np.abs(matrix)
    provable = (
        np.isfinite(matrix)
        & (absx >= 1e-14)
        & (absx <= 1e14)
        # 0 and 1 may be bools coerced by np.asarray — leave them to the
        # scalar rule, which sees the original object's type.
        & (matrix != 0.0)
        & (matrix != 1.0)
    )

    x = np.where(provable, matrix, 1.0)  # neutral filler for the math below
    exponent = np.floor(np.log10(np.abs(x))).astype(np.int64)
    shift = (MAX_SIGNIFICANT_DIGITS - 1) - exponent

    up = np.power(10.0, np.maximum(shift, 0))
    down = np.power(10.0, np.maximum(-shift, 0))
    mantissa = np.round(x * up / down)

    fits_cap = np.abs(mantissa) < 10**MAX_SIGNIFICANT_DIGITS
    round_trips = mantissa * down / up == x
    return np.asarray(provable & fits_cap & round_trips)


def validate_response_type(response) -> typing.Optional[str]:
    # check if the response is empty
    if response is None:
        return "Response is empty"

    if not isinstance(response, (tuple, list)):
        return f"Response format is incorrect: expected tuple or list, got {type(response)}"

    if len(response) == 0:
        return "Response is empty"

    if not isinstance(response[0], int):
        return f"Start time format is incorrect: expected int, got {type(response[0])}"

    if not isinstance(response[1], int):
        return f"Time increment format is incorrect: expected int, got {type(response[1])}"

    return None


def validate_responses(
    response,
    simulation_input: SimulationInput,
    process_time_str: typing.Optional[str],
) -> str:
    """
    Validate responses from miners.

    Return a string with the error message
    if the response is not following the expected format or the response is empty,
    otherwise, return "CORRECT".
    """
    # check the process time
    if process_time_str is None:
        return "time out or internal server error (process time is None)"

    start_time = datetime.fromisoformat(simulation_input.start_time)

    error_message = validate_response_type(response)
    if error_message:
        return error_message

    # check the start time
    first_time_timestamp: int = response[0]
    expected_first_time_timestamp = int(start_time.timestamp())
    if first_time_timestamp != expected_first_time_timestamp:
        return f"Start time timestamp is incorrect: expected {expected_first_time_timestamp}, got {first_time_timestamp}"

    # check the time increment
    time_increment: int = response[1]
    expected_time_increment = simulation_input.time_increment
    if time_increment != expected_time_increment:
        return f"Time increment is incorrect: expected {expected_time_increment}, got {time_increment}"

    number_of_paths = len(response[2:])
    # check the number of paths
    if number_of_paths != simulation_input.num_simulations:
        return f"Number of paths is incorrect: expected {simulation_input.num_simulations}, got {number_of_paths}"

    all_paths = response[2:]
    expected_time_points = (
        simulation_input.time_length // simulation_input.time_increment + 1
    )

    error_message = _validate_all_paths(all_paths, expected_time_points)
    if error_message:
        return error_message

    return CORRECT


def _validate_all_paths(
    all_paths, expected_time_points: int
) -> typing.Optional[str]:
    """Validate every path's shape and points, first offender wins.

    Points are bulk-validated in one NumPy pass; anything not provably
    valid is re-checked with the exact scalar rule in original scan order.
    """
    for path in all_paths:
        if not isinstance(path, list):
            return f"Path format is incorrect: expected list, got {type(path)}"
        if len(path) != expected_time_points:
            return f"Number of time points is incorrect: expected {expected_time_points}, got {len(path)}"

    try:
        matrix = np.asarray(all_paths)
    except ValueError:  # ragged nesting (a point that is a sequence)
        matrix = None

    if matrix is None or matrix.ndim != 2 or matrix.dtype.kind not in "iuf":
        return _validate_points_exact(all_paths)

    proven = _sig_digits_pass_mask(matrix)
    if proven.all():
        return None

    for i, j in zip(*np.nonzero(~proven)):
        error_message = _point_error(all_paths[i][j])
        if error_message:
            return error_message
    return None


def _validate_points_exact(all_paths) -> typing.Optional[str]:
    for path in all_paths:
        for point in path:
            error_message = _point_error(point)
            if error_message:
                return error_message
    return None

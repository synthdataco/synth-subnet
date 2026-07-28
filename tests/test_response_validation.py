from datetime import datetime, timezone
import math
import struct

from synth.simulation_input import SimulationInput
from synth.validator.response_validation_v2 import validate_responses, CORRECT

start_time = datetime.fromisoformat("2023-01-01T00:00:00").replace(
    tzinfo=timezone.utc
)
time_increment = 1


def test_validate_responses_process_time_none():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )
    response: list = []
    process_time_str = None

    result = validate_responses(response, simulation_input, process_time_str)
    assert result == "time out or internal server error (process time is None)"


def test_validate_responses_empty_response():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )
    response = ()
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert result == "Response is empty"


def test_validate_responses_incorrect_type():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )
    response: dict = {
        "time": int(start_time.timestamp()),
        "increment": time_increment,
        "paths": [[123.45] * 11],
    }
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert (
        result
        == "Response format is incorrect: expected tuple or list, got <class 'dict'>"
    )


def test_validate_responses_incorrect_number_of_paths():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=2,
        time_length=10,
        time_increment=time_increment,
    )
    response = (int(start_time.timestamp()), time_increment)
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert result == "Number of paths is incorrect: expected 2, got 0"

    response2 = (int(start_time.timestamp()), time_increment, [123.45])
    process_time_str = "0"

    result = validate_responses(response2, simulation_input, process_time_str)
    assert result == "Number of paths is incorrect: expected 2, got 1"


def test_validate_responses_incorrect_path_type():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=2,
        time_length=1,
        time_increment=time_increment,
    )
    response = (
        int(start_time.timestamp()),
        time_increment,
        {"price": 123.45},
        {"price": 123.45},
    )
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert (
        result == "Path format is incorrect: expected list, got <class 'dict'>"
    )


def test_validate_responses_incorrect_number_of_time_points():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )
    response = (int(start_time.timestamp()), time_increment, [123.45])
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert result == "Number of time points is incorrect: expected 11, got 1"


def test_validate_responses_incorrect_start_time():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )

    response = (start_time.isoformat(), time_increment, [123.45] * 11)
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert (
        result
        == "Start time format is incorrect: expected int, got <class 'str'>"
    )

    response2 = (start_time.timestamp(), time_increment, [123.45] * 11)
    process_time_str = "0"

    result = validate_responses(response2, simulation_input, process_time_str)
    assert (
        result
        == "Start time format is incorrect: expected int, got <class 'float'>"
    )

    response3 = (
        int(start_time.timestamp()) + 1,
        time_increment,
        [123.45] * 11,
    )
    process_time_str = "0"

    result = validate_responses(response3, simulation_input, process_time_str)
    assert (
        result
        == "Start time timestamp is incorrect: expected 1672531200, got 1672531201"
    )


def test_validate_responses_incorrect_time_increment():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )
    response = (int(start_time.timestamp()), "", [123.45] * 11)
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert (
        result
        == "Time increment format is incorrect: expected int, got <class 'str'>"
    )

    response2 = (
        int(start_time.timestamp()),
        time_increment + 1,
        [123.45] * 11,
    )
    process_time_str = "0"

    result = validate_responses(response2, simulation_input, process_time_str)
    assert result == "Time increment is incorrect: expected 1, got 2"


def test_validate_responses_incorrect_price_format():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )
    response = (
        int(start_time.timestamp()),
        time_increment,
        ["123.45"] * 11,
    )
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert (
        result
        == "Price format is incorrect: expected int or float, got <class 'str'>"
    )


def test_validate_responses_incorrect_price_digits():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=10,
        time_increment=time_increment,
    )
    response = (
        int(start_time.timestamp()),
        time_increment,
        [123.456789] * 11,
    )
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert result == "Price format is incorrect: too many digits 123.456789"


def test_validate_responses_correct():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=3,
        time_increment=time_increment,
    )
    response: tuple = (
        int(start_time.timestamp()),
        time_increment,
        [123.45678] * 4,
    )
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert result == CORRECT


def test_validate_responses_correct_2():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=3,
        time_increment=time_increment,
    )
    response: list = [
        int(start_time.timestamp()),
        time_increment,
        [123.45678] * 4,
    ]
    process_time_str = "0"

    result = validate_responses(response, simulation_input, process_time_str)
    assert result == CORRECT


# --- vectorised significant-digit rule parity ----------------------------
#
# validate_responses proves most points valid in one NumPy pass and falls
# back to the exact scalar rule for the rest. These tests pin the combined
# result against the scalar rule, restated verbatim below as the oracle,
# on values chosen to stress every boundary of the vectorised proof.


def _reference_point_verdict(point):
    """The per-point rule, restated verbatim as the oracle: int/float
    (bools excluded), finite, float32-representable, at most 8 significant
    digits."""
    if isinstance(point, bool) or not isinstance(point, (int, float)):
        return f"Price format is incorrect: expected int or float, got {type(point)}"
    try:
        value = float(point)
    except OverflowError:
        return f"Price format is incorrect: too many digits {point}"
    if not math.isfinite(value):
        return f"Price format is incorrect: non-finite value {point}"
    try:
        as_float32 = struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return f"Price format is incorrect: exceeds float32 range {point}"
    if as_float32 == 0.0 and value != 0.0:
        return f"Price format is incorrect: underflows float32 {point}"
    if float(f"{value:.7e}") != value:
        return f"Price format is incorrect: too many digits {point}"
    return None


_DIGIT_RULE_EDGE_CASES = [
    # around the 8-significant-digit budget
    123.45678,  # 8 sig digits -> valid
    123.456789,  # 9 sig digits -> invalid
    1234567.8,  # valid
    9999999.5,  # valid
    9999999.25,  # 9 sig digits -> invalid
    12345678.0,  # 8 sig digits -> valid (trailing .0 is not significant)
    10000000.0,  # 1 sig digit -> valid
    1.0,
    0.5,
    # magnitude does not matter, only significant digits
    0.0001234,
    0.00012345678,  # 8 sig digits -> valid
    0.000123456789,  # 9 sig digits -> invalid
    1e-05,
    1.2345e-05,
    1.23456789e-05,  # 9 sig digits -> invalid
    1e16,
    1.2345e20,
    1.23456789e20,  # 9 sig digits -> invalid
    # ints follow the same rule, evaluated on their float value
    12345678,  # valid
    123456789,  # 9 sig digits -> invalid
    99999999,
    100000000,  # 1 sig digit -> valid
    0,
    # the sign is not a digit
    -123.45,
    -1234567.8,  # valid
    -123.456789,  # invalid
    -99999999,
    # non-finite values are rejected
    float("nan"),
    float("inf"),
    float("-inf"),
    # float artefacts and extremes (outside the provable band -> fallback)
    0.1 + 0.2,  # 0.30000000000000004 -> invalid
    2**53 + 1.0,  # 16 sig digits -> invalid
    5e-324,  # underflows float32 -> invalid
    1e-20,  # 1 sig digit -> valid, below the provable band
    1e20,  # 1 sig digit -> valid, above the provable band
    10**400,  # int beyond float64 range -> rejected, must not crash
    # float32 range
    3.4e38,  # just under float32 max -> valid
    3.5e38,  # invalid
    1e39,  # invalid
    -1e39,  # invalid
    1e300,  # invalid
    1e-45,  # rounds to the smallest subnormal -> valid
    1e-300,  # collapses to 0 -> invalid
]


def _single_path_response(points):
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=1,
        time_length=len(points) - 1,
        time_increment=time_increment,
    )
    response = (int(start_time.timestamp()), time_increment, list(points))
    return response, simulation_input


def test_digit_rule_matches_reference_on_edge_cases():
    for value in _DIGIT_RULE_EDGE_CASES:
        response, simulation_input = _single_path_response(
            [value, 123.45, 123.45]
        )
        expected = _reference_point_verdict(value) or CORRECT
        result = validate_responses(response, simulation_input, "0")
        assert (
            result == expected
        ), f"value {value!r}: expected {expected!r}, got {result!r}"


def test_first_offending_point_is_reported():
    # 1e-20 is valid but below the mask's provable band; the offender
    # after it must still be the one reported, in original scan order.
    response, simulation_input = _single_path_response(
        [1e-20, 123.456789, 999.9999999]
    )
    result = validate_responses(response, simulation_input, "0")
    assert result == "Price format is incorrect: too many digits 123.456789"


def test_non_finite_points_are_rejected():
    response, simulation_input = _single_path_response(
        [123.45, float("nan"), 123.45]
    )
    result = validate_responses(response, simulation_input, "0")
    assert result == "Price format is incorrect: non-finite value nan"


def test_point_beyond_float32_range_is_rejected():
    response, simulation_input = _single_path_response([123.45, 1e39, 123.45])
    result = validate_responses(response, simulation_input, "0")
    assert result == "Price format is incorrect: exceeds float32 range 1e+39"


def test_point_that_underflows_float32_is_rejected():
    response, simulation_input = _single_path_response(
        [123.45, 1e-300, 123.45]
    )
    result = validate_responses(response, simulation_input, "0")
    assert result == "Price format is incorrect: underflows float32 1e-300"


def test_non_numeric_point_in_mixed_paths():
    simulation_input = SimulationInput(
        start_time=start_time.isoformat(),
        num_simulations=2,
        time_length=2,
        time_increment=time_increment,
    )
    response = (
        int(start_time.timestamp()),
        time_increment,
        [123.45, 123.45, 123.45],
        [123.45, "123.45", 123.45],
    )
    result = validate_responses(response, simulation_input, "0")
    assert (
        result
        == "Price format is incorrect: expected int or float, got <class 'str'>"
    )


def test_point_that_is_a_list_is_rejected():
    # Equal-length nested lists coerce to a 3-D numeric array; the type
    # error must still be reported per point.
    response, simulation_input = _single_path_response(
        [[1.0, 2.0], [3.0, 4.0]]
    )
    result = validate_responses(response, simulation_input, "0")
    assert (
        result
        == "Price format is incorrect: expected int or float, got <class 'list'>"
    )


def test_bool_points_are_rejected():
    response, simulation_input = _single_path_response([True, False, True])
    result = validate_responses(response, simulation_input, "0")
    assert (
        result
        == "Price format is incorrect: expected int or float, got <class 'bool'>"
    )


def test_bool_hidden_among_floats_is_rejected():
    # np.asarray coerces True to 1.0 in a float path; the mask must not
    # prove values 0/1 so the original bool reaches the exact type check.
    response, simulation_input = _single_path_response([123.45, True, 123.45])
    result = validate_responses(response, simulation_input, "0")
    assert (
        result
        == "Price format is incorrect: expected int or float, got <class 'bool'>"
    )


def test_prices_zero_and_one_are_still_valid():
    # 0/1 are unprovable by the mask (possible coerced bools) but real
    # ints/floats of those values must still validate via the fallback.
    response, simulation_input = _single_path_response([1.0, 0.0, 1, 0])
    assert validate_responses(response, simulation_input, "0") == CORRECT

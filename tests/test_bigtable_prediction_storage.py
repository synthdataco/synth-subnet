"""Unit tests for the Bigtable prediction storage backend.

Bigtable I/O is mocked: we verify routing, serialization, and skip-logic but
not the actual google-cloud-bigtable client behaviour.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

pytest.importorskip("google.cloud.bigtable")

from synth.simulation_input import SimulationInput  # noqa: E402
from synth.validator import prompt_config  # noqa: E402
from synth.validator import response_validation_v2  # noqa: E402
from synth.validator import bigtable_prediction_storage as bps  # noqa: E402

CORRECT = response_validation_v2.CORRECT
LOW_TIME_LENGTH = prompt_config.LOW_FREQUENCY.time_length
LOW_TIME_INCREMENT = prompt_config.LOW_FREQUENCY.time_increment
HIGH_TIME_LENGTH = prompt_config.HIGH_FREQUENCY.time_length
HIGH_TIME_INCREMENT = prompt_config.HIGH_FREQUENCY.time_increment


def _make_production_prediction(num_simulations: int, num_timesteps: int):
    """Build a prediction in production wire format.

    `[start_ts, time_increment, path1, path2, ...]` where each path is a
    list of `num_timesteps` floats.
    """
    rng = np.random.default_rng(0)
    paths = rng.uniform(
        50_000, 100_000, size=(num_simulations, num_timesteps)
    ).astype(np.float32)
    return [1700000000, 300, *paths.tolist()]


def _make_blob(num_simulations: int, num_timesteps: int) -> bytes:
    """Paths encoded the way write_predictions receives them."""
    prediction = _make_production_prediction(num_simulations, num_timesteps)
    return bps.encode_paths(prediction[2:])


def _make_storage_with_mock_tables():
    """Bypass __init__ so we don't need real env vars."""
    storage = bps.BigtablePredictionStorage.__new__(
        bps.BigtablePredictionStorage
    )
    storage._table_low_id = "tbl_low"
    storage._table_high_id = "tbl_high"
    low = MagicMock()
    low.table_id = "tbl_low"
    high = MagicMock()
    high.table_id = "tbl_high"
    storage._tables = {"low": low, "high": high}
    return storage


def test_probe_connectivity_passes_when_tables_reachable():
    storage = _make_storage_with_mock_tables()
    # read_row succeeds (returns None for non-existent key) → no raise
    storage._tables["low"].read_row.return_value = None
    storage._tables["high"].read_row.return_value = None
    storage._probe_connectivity("proj", "inst")  # no exception


def test_probe_connectivity_raises_on_missing_table():
    from google.api_core import exceptions as gapi

    storage = _make_storage_with_mock_tables()
    storage._tables["low"].read_row.side_effect = gapi.NotFound("table gone")
    with pytest.raises(RuntimeError, match="not found"):
        storage._probe_connectivity("proj", "inst")


def test_probe_connectivity_raises_on_permission_denied():
    from google.api_core import exceptions as gapi

    storage = _make_storage_with_mock_tables()
    storage._tables["low"].read_row.side_effect = gapi.PermissionDenied("nope")
    with pytest.raises(RuntimeError, match="permission denied"):
        storage._probe_connectivity("proj", "inst")


def test_build_row_key_format():
    key = bps.BigtablePredictionStorage.build_row_key("BTC", 1779710400, 42)
    # miner_id is zero-padded so range scans return rows in numeric order
    assert key == "BTC#1779710400#000042"


def test_paths_round_trip():
    prediction = _make_production_prediction(
        num_simulations=4, num_timesteps=7
    )
    blob = bps.encode_paths(prediction[2:])

    paths = bps._float32_bytes_to_paths(
        blob, num_simulations=4, num_timesteps=7
    )

    expected = np.asarray(prediction[2:], dtype=np.float32).tolist()
    assert paths == expected


@pytest.mark.parametrize(
    "paths",
    [
        [[65432.17, 65433.5, 0.12345678], [1.5, 2.25, 99999.99]],  # float
        [[65432, 65433, 12], [1, 2, 99999]],  # int matrix
        [[65432, 65433.5, 12], [1.5, 2, 99999.99]],  # mixed
        [[0, 1, 1.0], [0.0, 2, 3]],  # points rechecked by the scalar rule
        # int64 -> float32 would round these differently from the wire values
        [[15728639999999999, 1, 2], [18874368000000001, 3, 4]],
    ],
)
def test_encode_paths_from_validation_matches_wire_lists(paths):
    sim_input = SimulationInput(
        start_time="2026-05-25T12:00:00+00:00",
        time_increment=1,
        time_length=2,
        num_simulations=2,
    )
    response = [int(1779710400), 1, *paths]

    format_validation, validated = (
        response_validation_v2.validate_responses_with_paths(
            response, sim_input, "1.0"
        )
    )

    assert format_validation == CORRECT
    assert bps.encode_paths(validated) == bps.encode_paths(paths)


def test_validate_responses_with_paths_returns_no_paths_when_invalid():
    sim_input = SimulationInput(
        start_time="2026-05-25T12:00:00+00:00",
        time_increment=1,
        time_length=2,
        num_simulations=1,
    )
    response = [1779710400, 1, [1.123456789, 2.0, 3.0]]

    format_validation, validated = (
        response_validation_v2.validate_responses_with_paths(
            response, sim_input, "1.0"
        )
    )

    assert format_validation != CORRECT
    assert validated is None


def _low_sim_input():
    return SimulationInput(
        asset="BTC",
        start_time="2026-05-25T12:00:00",
        time_increment=LOW_TIME_INCREMENT,
        time_length=LOW_TIME_LENGTH,
        num_simulations=2,
    )


def _high_sim_input():
    return SimulationInput(
        asset="ETH",
        start_time="2026-05-25T12:00:00",
        time_increment=HIGH_TIME_INCREMENT,
        time_length=HIGH_TIME_LENGTH,
        num_simulations=2,
    )


def _validator_request(
    time_length, time_increment, num_simulations, asset="BTC", start_time=None
):
    from datetime import datetime, timezone

    if start_time is None:
        start_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    return MagicMock(
        asset=asset,
        start_time=start_time,
        time_length=time_length,
        time_increment=time_increment,
        num_simulations=num_simulations,
    )


def _expected_key_for(sim_input, miner_id):
    start_unix = bps._start_time_to_unix(sim_input.start_time)
    return bps.BigtablePredictionStorage.build_row_key(
        sim_input.asset, start_unix, miner_id
    )


def test_write_predictions_skips_invalid_format_and_unknown_miners():
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    ok = MagicMock()
    ok.code = 0
    storage._tables["low"].mutate_rows.return_value = [ok]

    good = _make_blob(2, 3)
    bad = _make_blob(2, 3)
    sim_input = _low_sim_input()
    miner_predictions = {
        10: (good, CORRECT, "1.0"),
        11: (bad, "time out or internal server error", "1.5"),
        # uid 12 is not in miner_id_map below — should be skipped
        12: (good, CORRECT, "1.0"),
    }
    miner_id_map = {10: 100, 11: 101}

    keys = storage.write_predictions(
        simulation_input=sim_input,
        miner_predictions=miner_predictions,
        miner_id_map=miner_id_map,
    )

    # only miner 10 was CORRECT *and* in miner_id_map
    assert list(keys.keys()) == [10]
    assert keys[10] == _expected_key_for(sim_input, 100)
    storage._tables["high"].mutate_rows.assert_not_called()
    assert storage._tables["low"].mutate_rows.call_count == 1


def test_write_predictions_routes_to_high_table():
    storage = _make_storage_with_mock_tables()
    storage._tables["high"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    ok = MagicMock()
    ok.code = 0
    storage._tables["high"].mutate_rows.return_value = [ok]

    prediction = _make_blob(2, 3)
    sim_input = _high_sim_input()
    storage.write_predictions(
        simulation_input=sim_input,
        miner_predictions={10: (prediction, CORRECT, "0.9")},
        miner_id_map={10: 100},
    )

    storage._tables["low"].mutate_rows.assert_not_called()
    storage._tables["high"].mutate_rows.assert_called_once()


def test_write_predictions_treats_none_status_as_failure():
    """The Bigtable SDK can return `None` for a row when it has no per-row
    response to report (e.g. transport hiccup). Treat that as a failure —
    we cannot confirm the write landed."""
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    storage._tables["low"].mutate_rows.return_value = [None]

    sim_input = _low_sim_input()
    prediction = _make_blob(2, 3)
    with pytest.raises(RuntimeError):
        storage.write_predictions(
            simulation_input=sim_input,
            miner_predictions={10: (prediction, CORRECT, "1.0")},
            miner_id_map={10: 100},
        )


def test_write_predictions_raises_on_short_status_list():
    """If the SDK returns fewer statuses than rows we sent, we can't tell
    which landed. Treat the whole batch as indeterminate."""
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    ok = MagicMock()
    ok.code = 0
    # 2 rows sent, only 1 status returned
    storage._tables["low"].mutate_rows.return_value = [ok]

    sim_input = _low_sim_input()
    prediction = _make_blob(2, 3)
    with pytest.raises(RuntimeError):
        storage.write_predictions(
            simulation_input=sim_input,
            miner_predictions={
                10: (prediction, CORRECT, "1.0"),
                11: (prediction, CORRECT, "1.0"),
            },
            miner_id_map={10: 100, 11: 101},
        )


def test_write_predictions_raises_when_any_mutate_fails():
    """Failed Bigtable writes must surface so save_responses' @retry kicks
    in. Returning a key for a row whose blob never landed would silently
    drop scoring data later."""
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    bad = MagicMock()
    bad.code = 13  # any non-zero
    bad.message = "boom"
    storage._tables["low"].mutate_rows.return_value = [bad]

    sim_input = _low_sim_input()
    prediction = _make_blob(2, 3)
    with pytest.raises(RuntimeError):
        storage.write_predictions(
            simulation_input=sim_input,
            miner_predictions={10: (prediction, CORRECT, "1.0")},
            miner_id_map={10: 100},
        )


def test_write_predictions_chunks_large_batches(monkeypatch):
    """A single mutate_rows RPC must stay under the server's 260 MiB limit.
    With many large `low` rows the batch is split across multiple RPCs and
    the per-row statuses are stitched back together in order."""
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )

    # Force tiny chunks so 3 rows split into 3 separate RPCs: each row's
    # blob is 2 sims x 3 steps x 4 bytes = 24 bytes; cap below 2 rows' worth.
    monkeypatch.setattr(bps, "_MAX_MUTATE_BYTES", 30)

    def _statuses_for(chunk):
        return [MagicMock(code=0) for _ in chunk]

    storage._tables["low"].mutate_rows.side_effect = _statuses_for

    prediction = _make_blob(2, 3)
    sim_input = _low_sim_input()
    miner_predictions = {
        uid: (prediction, CORRECT, "1.0") for uid in (10, 11, 12)
    }
    miner_id_map = {10: 100, 11: 101, 12: 102}

    keys = storage.write_predictions(
        simulation_input=sim_input,
        miner_predictions=miner_predictions,
        miner_id_map=miner_id_map,
    )

    # All three miners committed, despite the batch being split.
    assert set(keys.keys()) == {10, 11, 12}
    # 24 bytes/row, 30-byte cap → one row per RPC → 3 calls.
    assert storage._tables["low"].mutate_rows.call_count == 3


def test_read_predictions_missing_rows_return_empty():
    storage = _make_storage_with_mock_tables()
    # Range scan returns no rows — every requested key stays [].
    storage._tables["low"].read_rows.return_value = iter([])
    vr = _validator_request(LOW_TIME_LENGTH, LOW_TIME_INCREMENT, 2)
    keys = ["k1", "k2"]

    result = storage.read_predictions(vr, keys)

    assert result == {"k1": [], "k2": []}


LOW_NUM_STEPS = LOW_TIME_LENGTH // LOW_TIME_INCREMENT + 1


def test_read_predictions_decodes_cell_bytes():
    storage = _make_storage_with_mock_tables()
    num_sims = 2
    prediction = _make_production_prediction(num_sims, LOW_NUM_STEPS)
    blob = bps.encode_paths(prediction[2:])

    vr = _validator_request(LOW_TIME_LENGTH, LOW_TIME_INCREMENT, num_sims)
    start_unix = int(vr.start_time.timestamp())
    key = bps.BigtablePredictionStorage.build_row_key(
        vr.asset, start_unix, 100
    )

    cell = MagicMock()
    cell.value = blob
    bt_row = MagicMock()
    bt_row.row_key = key.encode("utf-8")
    bt_row.cells = {bps.COLUMN_FAMILY: {bps.COLUMN_QUALIFIER: [cell]}}
    storage._tables["low"].read_rows.return_value = iter([bt_row])

    result = storage.read_predictions(vr, [key])

    expected = np.asarray(prediction[2:], dtype=np.float32).tolist()
    assert result[key] == expected


def test_read_predictions_ignores_unwanted_keys_from_range_scan():
    """The range scan also surfaces rows whose Postgres siblings were
    soft-deleted; we should ignore those, not return them in the result."""
    storage = _make_storage_with_mock_tables()
    num_sims = 2
    prediction = _make_production_prediction(num_sims, LOW_NUM_STEPS)
    blob = bps.encode_paths(prediction[2:])

    vr = _validator_request(LOW_TIME_LENGTH, LOW_TIME_INCREMENT, num_sims)
    start_unix = int(vr.start_time.timestamp())
    wanted = bps.BigtablePredictionStorage.build_row_key(
        vr.asset, start_unix, 100
    )
    unwanted = bps.BigtablePredictionStorage.build_row_key(
        vr.asset, start_unix, 999
    )

    def _row(key):
        cell = MagicMock()
        cell.value = blob
        r = MagicMock()
        r.row_key = key.encode("utf-8")
        r.cells = {bps.COLUMN_FAMILY: {bps.COLUMN_QUALIFIER: [cell]}}
        return r

    storage._tables["low"].read_rows.return_value = iter(
        [_row(wanted), _row(unwanted)]
    )

    result = storage.read_predictions(vr, [wanted])

    assert set(result.keys()) == {wanted}


def test_read_predictions_skips_undecodable_blobs():
    storage = _make_storage_with_mock_tables()
    vr = _validator_request(LOW_TIME_LENGTH, LOW_TIME_INCREMENT, 2)
    start_unix = int(vr.start_time.timestamp())
    key = bps.BigtablePredictionStorage.build_row_key(
        vr.asset, start_unix, 100
    )

    # Three bytes — not a multiple of 4, so np.frombuffer raises.
    cell = MagicMock()
    cell.value = b"\x00\x01\x02"
    bt_row = MagicMock()
    bt_row.row_key = key.encode("utf-8")
    bt_row.cells = {bps.COLUMN_FAMILY: {bps.COLUMN_QUALIFIER: [cell]}}
    storage._tables["low"].read_rows.return_value = iter([bt_row])

    result = storage.read_predictions(vr, [key])
    assert result[key] == []


def test_label_from_time_length_raises_on_unknown():
    with pytest.raises(ValueError):
        prompt_config.label_from_time_length(time_length=42)


def test_start_time_to_unix_treats_naive_as_utc():
    # Naive matches +00:00 (no Z support needed — callers always pass
    # `simulation_input.start_time` which is an isoformat() string).
    base = bps._start_time_to_unix("2026-05-25T12:00:00")
    assert bps._start_time_to_unix("2026-05-25T12:00:00+00:00") == base
    assert bps._start_time_to_unix("2026-05-25T12:00:00") == 1779710400


def test_delete_predictions_routes_to_low_table():
    storage = _make_storage_with_mock_tables()
    rows_by_key = {}

    def _direct_row(key):
        row = MagicMock(key=key)
        rows_by_key[key] = row
        return row

    storage._tables["low"].direct_row.side_effect = _direct_row
    storage._tables["low"].mutate_rows.side_effect = lambda chunk: [
        MagicMock(code=0) for _ in chunk
    ]

    storage.delete_predictions(LOW_TIME_LENGTH, ["k1", "k2"])

    # One DeleteFromRow mutation per key, sent to the low table only.
    assert set(rows_by_key.keys()) == {"k1", "k2"}
    for row in rows_by_key.values():
        row.delete.assert_called_once_with()
    storage._tables["low"].mutate_rows.assert_called_once()
    storage._tables["high"].direct_row.assert_not_called()


def test_delete_predictions_routes_to_high_table():
    storage = _make_storage_with_mock_tables()
    storage._tables["high"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    storage._tables["high"].mutate_rows.side_effect = lambda chunk: [
        MagicMock(code=0) for _ in chunk
    ]

    storage.delete_predictions(HIGH_TIME_LENGTH, ["k1"])

    storage._tables["high"].mutate_rows.assert_called_once()
    storage._tables["low"].direct_row.assert_not_called()


def test_delete_predictions_empty_keys_is_noop():
    storage = _make_storage_with_mock_tables()

    storage.delete_predictions(LOW_TIME_LENGTH, [])

    storage._tables["low"].mutate_rows.assert_not_called()
    storage._tables["high"].mutate_rows.assert_not_called()


def test_delete_predictions_chunks_by_row_count(monkeypatch):
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    storage._tables["low"].mutate_rows.side_effect = lambda chunk: [
        MagicMock(code=0) for _ in chunk
    ]
    monkeypatch.setattr(bps, "_DELETE_CHUNK_ROWS", 2)

    storage.delete_predictions(LOW_TIME_LENGTH, ["k1", "k2", "k3"])

    # 3 keys, 2 per chunk → 2 RPCs.
    assert storage._tables["low"].mutate_rows.call_count == 2


def test_delete_predictions_raises_when_any_mutate_fails():
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    bad = MagicMock()
    bad.code = 13  # any non-zero
    bad.message = "boom"
    storage._tables["low"].mutate_rows.return_value = [
        MagicMock(code=0),
        bad,
    ]

    with pytest.raises(RuntimeError):
        storage.delete_predictions(LOW_TIME_LENGTH, ["k1", "k2"])


def test_delete_predictions_treats_none_status_as_failure():
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    storage._tables["low"].mutate_rows.return_value = [None]

    with pytest.raises(RuntimeError):
        storage.delete_predictions(LOW_TIME_LENGTH, ["k1"])


def test_delete_predictions_raises_on_short_status_list():
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.side_effect = lambda key: MagicMock(
        key=key
    )
    storage._tables["low"].mutate_rows.return_value = [MagicMock(code=0)]

    with pytest.raises(RuntimeError, match="statuses"):
        storage.delete_predictions(LOW_TIME_LENGTH, ["k1", "k2"])


def test_row_key_matches_build_row_key():
    storage = _make_storage_with_mock_tables()
    sim_input = _low_sim_input()

    assert storage.row_key(sim_input, 100) == _expected_key_for(sim_input, 100)


def test_write_prediction_commits_one_row_to_label_table():
    storage = _make_storage_with_mock_tables()
    row = storage._tables["high"].direct_row.return_value
    row.commit.return_value = MagicMock(code=0)

    storage.write_prediction(HIGH_TIME_LENGTH, "ETH#1#000100", b"blob")

    storage._tables["high"].direct_row.assert_called_once_with("ETH#1#000100")
    row.set_cell.assert_called_once_with(
        bps.COLUMN_FAMILY, bps.COLUMN_QUALIFIER, b"blob"
    )
    storage._tables["low"].direct_row.assert_not_called()


@pytest.mark.parametrize("status", [None, MagicMock(code=13, message="x")])
def test_write_prediction_raises_unless_confirmed(status):
    storage = _make_storage_with_mock_tables()
    storage._tables["low"].direct_row.return_value.commit.return_value = status

    with pytest.raises(RuntimeError):
        storage.write_prediction(LOW_TIME_LENGTH, "BTC#1#000100", b"blob")

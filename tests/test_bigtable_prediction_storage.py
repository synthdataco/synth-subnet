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
    blob = bps._paths_to_float32_bytes(prediction)

    paths = bps._float32_bytes_to_paths(
        blob, num_simulations=4, num_timesteps=7
    )

    expected = np.asarray(prediction[2:], dtype=np.float32).tolist()
    assert paths == expected


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

    good = _make_production_prediction(2, 3)
    bad = _make_production_prediction(2, 3)
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

    prediction = _make_production_prediction(2, 3)
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
    prediction = _make_production_prediction(2, 3)
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
    prediction = _make_production_prediction(2, 3)
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
    prediction = _make_production_prediction(2, 3)
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

    prediction = _make_production_prediction(2, 3)
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
    blob = bps._paths_to_float32_bytes(prediction)

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
    blob = bps._paths_to_float32_bytes(prediction)

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

import asyncio
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock

import bittensor as bt
import pytest

from synth.base import dendrite_multiprocess
from synth.protocol import Simulation
from synth.simulation_input import SimulationInput
from synth.validator.bigtable_prediction_storage import encode_paths
from synth.validator.response_validation_v2 import CORRECT, validate_responses


def _fake_axon(uid: int):
    return SimpleNamespace(to_parameter_dict=lambda: {"uid": uid})


def _forward(axons, nprocs):
    return dendrite_multiprocess.sync_forward_multiprocess(
        keypair=SimpleNamespace(ss58_address="ss58"),
        uuid="uuid",
        external_ip="1.2.3.4",
        axons=axons,
        synapse=Simulation(simulation_input=SimulationInput()),
        timeout=1.0,
        nprocs=nprocs,
    )


@pytest.fixture
def stub_signing(monkeypatch):
    monkeypatch.setattr(
        dendrite_multiprocess,
        "sign_axons",
        lambda keypair, nonce, uuid, external_ip, axons, synapse, timeout: (
            f"sig-{i}" for i in range(len(axons))
        ),
    )


def test_results_keep_axon_order_when_chunks_finish_out_of_order(
    monkeypatch, stub_signing
):
    def fake_run_chunk(*args):
        axon_requests = args[6]
        # Earlier chunks finish last.
        time.sleep(0.05 * (10 - axon_requests[0][0]["uid"]) / 10)
        return [[[axon["uid"]], "0.1"] for axon, _, _ in axon_requests]

    monkeypatch.setattr(dendrite_multiprocess, "run_chunk", fake_run_chunk)
    with ThreadPoolExecutor(4) as executor:
        monkeypatch.setattr(
            dendrite_multiprocess,
            "get_process_executor",
            lambda nprocs: executor,
        )
        results = _forward([_fake_axon(uid) for uid in range(10)], nprocs=4)

    assert results == [[[uid], "0.1"] for uid in range(10)]


def test_failed_chunk_waits_for_running_siblings(monkeypatch, stub_signing):
    started, finished = [], []

    def fake_run_chunk(*args):
        uid = args[6][0][0]["uid"]
        started.append(uid)
        if uid == 0:
            # Fail once the sibling is running.
            time.sleep(0.05)
            raise ValueError("chunk failed")
        time.sleep(0.2)
        finished.append(uid)
        return []

    monkeypatch.setattr(dendrite_multiprocess, "run_chunk", fake_run_chunk)
    with ThreadPoolExecutor(2) as executor:
        monkeypatch.setattr(
            dendrite_multiprocess,
            "get_process_executor",
            lambda nprocs: executor,
        )
        with pytest.raises(ValueError):
            _forward([_fake_axon(uid) for uid in range(2)], nprocs=2)
        assert sorted(started) == [0, 1]
        assert finished == [1]


def test_broken_pool_is_dropped_for_next_cycle(monkeypatch, stub_signing):
    class BrokenExecutor:
        def submit(self, *args):
            future: Future = Future()
            future.set_exception(BrokenProcessPool("worker died"))
            return future

    monkeypatch.setattr(
        dendrite_multiprocess, "_PROCESS_EXECUTOR", BrokenExecutor()
    )

    with pytest.raises(BrokenProcessPool):
        _forward([_fake_axon(0)], nprocs=1)

    assert dendrite_multiprocess._PROCESS_EXECUTOR is None


_SIM_INPUT = SimulationInput(
    start_time="2026-05-25T12:00:00+00:00",
    time_increment=1,
    time_length=2,
    num_simulations=2,
)
_PATHS = [[65432.17, 65433.5, 65431.0], [65430.25, 65429.0, 65428.75]]
_CORRECT_OUTPUT = [1779710400, 1, *_PATHS]


@pytest.mark.parametrize(
    "output, process_time",
    [
        (_CORRECT_OUTPUT, "1.0"),
        ([1779710400, 1, [1.123456789, 2.0, 3.0], _PATHS[1]], "1.0"),
        (None, None),  # timed out
    ],
)
def test_validate_output_matches_validate_responses(output, process_time):
    result = dendrite_multiprocess.validate_output(
        output, _SIM_INPUT, process_time, float32_output=False
    )

    assert result == [
        output,
        validate_responses(output, _SIM_INPUT, process_time),
        process_time,
    ]


def test_validate_output_returns_float32_blob_when_correct():
    result = dendrite_multiprocess.validate_output(
        _CORRECT_OUTPUT, _SIM_INPUT, "1.0", float32_output=True
    )

    assert result == [encode_paths(_PATHS), CORRECT, "1.0"]


def test_validate_output_drops_invalid_output_as_float32():
    result = dendrite_multiprocess.validate_output(
        [1779710400, 1, [1.123456789, 2.0, 3.0], _PATHS[1]],
        _SIM_INPUT,
        "1.0",
        float32_output=True,
    )

    assert result[0] is None
    assert result[1] != CORRECT


def test_validate_output_survives_validation_crash(monkeypatch):
    def crash(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        dendrite_multiprocess, "validate_responses_with_paths", crash
    )

    result = dendrite_multiprocess.validate_output(
        _CORRECT_OUTPUT, _SIM_INPUT, "1.0", float32_output=True
    )

    assert result == [None, "error during validation", "1.0"]


async def _result(*values):
    return list(values)


def test_store_prediction_writes_correct_blob_and_returns_key():
    storage = MagicMock()

    result = asyncio.run(
        dendrite_multiprocess.store_prediction(
            _result(b"blob", CORRECT, "1.0"), storage, 3600, "key"
        )
    )

    storage.write_prediction.assert_called_once_with(3600, "key", b"blob")
    assert result == [None, CORRECT, "1.0", "key"]


def test_store_prediction_keeps_blob_when_write_fails():
    storage = MagicMock()
    storage.write_prediction.side_effect = RuntimeError("boom")

    result = asyncio.run(
        dendrite_multiprocess.store_prediction(
            _result(b"blob", CORRECT, "1.0"), storage, 3600, "key"
        )
    )

    assert result == [b"blob", CORRECT, "1.0", None]


@pytest.mark.parametrize(
    "format_validation, row_key",
    [("Response is empty", "key"), (CORRECT, None)],
)
def test_store_prediction_skips_write(format_validation, row_key):
    storage = MagicMock()

    result = asyncio.run(
        dendrite_multiprocess.store_prediction(
            _result(b"blob", format_validation, "1.0"),
            storage,
            3600,
            row_key,
        )
    )

    storage.write_prediction.assert_not_called()
    assert result == [b"blob", format_validation, "1.0", None]


def test_row_keys_reach_workers_aligned_with_axons(monkeypatch, stub_signing):
    def fake_run_chunk(*args):
        return [[None, CORRECT, "0.1", row_key] for _, _, row_key in args[6]]

    monkeypatch.setattr(dendrite_multiprocess, "run_chunk", fake_run_chunk)
    with ThreadPoolExecutor(3) as executor:
        monkeypatch.setattr(
            dendrite_multiprocess,
            "get_process_executor",
            lambda nprocs: executor,
        )
        results = dendrite_multiprocess.sync_forward_multiprocess(
            keypair=SimpleNamespace(ss58_address="ss58"),
            uuid="uuid",
            external_ip="1.2.3.4",
            axons=[_fake_axon(uid) for uid in range(5)],
            synapse=Simulation(simulation_input=SimulationInput()),
            timeout=1.0,
            nprocs=3,
            float32_output=True,
            bigtable_row_keys=["k0", None, "k2", "k3", None],
        )

    assert [result[3] for result in results] == ["k0", None, "k2", "k3", None]


def _serve_miner(simulation_output):
    """Start a local HTTP miner answering every query with
    `simulation_output`; return the server."""

    class Miner(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {
                    "simulation_input": _SIM_INPUT.model_dump(),
                    "simulation_output": simulation_output,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Miner)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def real_pool(monkeypatch):
    monkeypatch.setattr(dendrite_multiprocess, "_PROCESS_EXECUTOR", None)
    yield
    if dendrite_multiprocess._PROCESS_EXECUTOR is not None:
        dendrite_multiprocess._PROCESS_EXECUTOR.shutdown(wait=True)


@pytest.mark.parametrize("float32_output", [False, True])
def test_real_pool_validates_and_encodes_in_workers(real_pool, float32_output):
    invalid_output = [1779710400, 1, [1.123456789, 2.0, 3.0], _PATHS[1]]
    servers = [_serve_miner(_CORRECT_OUTPUT), _serve_miner(invalid_output)]
    keypair = bt.Keypair.create_from_mnemonic(bt.Keypair.generate_mnemonic())
    axons = [
        bt.AxonInfo(
            version=1,
            ip="127.0.0.1",
            port=server.server_address[1],
            ip_type=4,
            hotkey=keypair.ss58_address,
            coldkey=keypair.ss58_address,
        )
        for server in servers
    ]

    try:
        results = dendrite_multiprocess.sync_forward_multiprocess(
            keypair,
            "uuid",
            "9.9.9.9",
            axons,
            Simulation(simulation_input=_SIM_INPUT),
            timeout=10.0,
            nprocs=2,
            float32_output=float32_output,
        )
    finally:
        for server in servers:
            server.shutdown()

    (correct, correct_fv, correct_pt, correct_key), (
        invalid,
        invalid_fv,
        _,
        _,
    ) = results
    assert correct_key is None
    assert correct_fv == CORRECT
    assert correct_pt is not None
    assert invalid_fv == validate_responses(invalid_output, _SIM_INPUT, "1")
    if float32_output:
        assert correct == encode_paths(_PATHS)
        assert invalid is None
    else:
        # pydantic parses the outer list as a tuple
        assert list(correct) == _CORRECT_OUTPUT
        assert list(invalid) == invalid_output

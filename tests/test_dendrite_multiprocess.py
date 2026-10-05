import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from types import SimpleNamespace

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
        axon_sig_pairs = args[6]
        # Earlier chunks finish last.
        time.sleep(0.05 * (10 - axon_sig_pairs[0][0]["uid"]) / 10)
        return [[[axon["uid"]], "0.1"] for axon, _ in axon_sig_pairs]

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

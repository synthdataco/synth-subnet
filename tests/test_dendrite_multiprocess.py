import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from types import SimpleNamespace

import pytest

from synth.base import dendrite_multiprocess
from synth.protocol import Simulation
from synth.simulation_input import SimulationInput


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

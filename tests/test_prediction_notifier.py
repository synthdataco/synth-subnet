from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from synth.simulation_input import SimulationInput
from synth.validator import forward as forward_module
from synth.validator.forward import query_available_miners_and_save_responses
from synth.validator.prediction_notifier import PredictionNotifier


class RecordingNotifier:
    def __init__(self):
        self.calls = []

    def publish_stored(self, validator_request_id, simulation_input):
        self.calls.append((validator_request_id, simulation_input))


def test_from_env_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("PUBSUB_PROJECT", raising=False)
    monkeypatch.delenv("PUBSUB_TOPIC_PREDICTIONS", raising=False)
    assert PredictionNotifier.from_env() is None

    # One var alone is not enough — both are required to enable.
    monkeypatch.setenv("PUBSUB_PROJECT", "some-project")
    assert PredictionNotifier.from_env() is None


def test_publish_stored_never_raises():
    # A publish failure must never propagate into the validator cycle.
    notifier = PredictionNotifier.__new__(PredictionNotifier)
    notifier._publisher = None  # publish() -> AttributeError
    notifier._topic_path = "projects/p/topics/t"
    notifier.publish_stored(
        validator_request_id=1,
        simulation_input=SimulationInput(asset="BTC"),
    )


def _run_forward(monkeypatch, save_return):
    """Drive query_available_miners_and_save_responses with one stubbed miner.

    Returns (saved_calls, notifier). The dendrite forward and response
    validation are stubbed; save_responses returns `save_return`.
    """
    fake_synapse = SimpleNamespace(
        deserialize=lambda: {"prediction": []},
        dendrite=SimpleNamespace(process_time="1.0"),
    )
    monkeypatch.setattr(
        forward_module,
        "sync_forward_multiprocess",
        lambda *args, **kwargs: [fake_synapse],
    )
    monkeypatch.setattr(
        forward_module, "validate_responses_v2", lambda *args: "CORRECT"
    )

    saved_calls = []

    class StubHandler:
        def save_responses(
            self, miner_predictions, simulation_input, request_time
        ):
            saved_calls.append(miner_predictions)
            return save_return

    base_neuron = SimpleNamespace(
        metagraph=SimpleNamespace(axons=["axon-0"]),
        dendrite=SimpleNamespace(
            keypair=None, uuid="uuid", external_ip="1.2.3.4"
        ),
        config=SimpleNamespace(neuron=SimpleNamespace(nprocs=1)),
    )
    simulation_input = SimulationInput(
        asset="BTC",
        start_time=(
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ).isoformat(),
        time_increment=60,
        time_length=3600,
        num_simulations=1,
    )
    notifier = RecordingNotifier()

    query_available_miners_and_save_responses(
        base_neuron=base_neuron,
        miner_data_handler=StubHandler(),
        miner_uids=[0],
        simulation_input=simulation_input,
        request_time=datetime.now(timezone.utc),
        prediction_notifier=notifier,
    )
    return saved_calls, notifier, simulation_input


def test_forward_notifies_with_committed_request_id(monkeypatch):
    saved_calls, notifier, simulation_input = _run_forward(
        monkeypatch, save_return=42
    )

    assert len(saved_calls) == 1
    assert notifier.calls == [(42, simulation_input)]


def test_forward_skips_notify_when_nothing_saved(monkeypatch):
    # save_responses returns None when no prediction record was stored.
    saved_calls, notifier, _ = _run_forward(monkeypatch, save_return=None)

    assert len(saved_calls) == 1
    assert notifier.calls == []

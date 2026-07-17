"""Optional Pub/Sub notification when a prediction cohort is stored.

When enabled, the validator publishes one message per stored validator
request so downstream consumers can react immediately instead of polling.
Enabled by setting both env vars:

    PUBSUB_PROJECT            : GCP project hosting the topic.
    PUBSUB_TOPIC_PREDICTIONS  : Topic id to publish to.

Publishing is strictly best-effort: any failure is logged and swallowed.
"""

import json
import os
import typing

import bittensor as bt
from google.cloud import pubsub_v1

from synth.simulation_input import SimulationInput


class PredictionNotifier:
    """Publish a message for each stored prediction cohort.

    Message body (JSON): validator_request_id, asset, time_length,
    start_time (ISO). `asset` and `time_length` are mirrored as attributes
    for subscription filtering.
    """

    def __init__(self, project: str, topic: str):
        self._publisher = pubsub_v1.PublisherClient()
        self._topic_path = self._publisher.topic_path(project, topic)
        bt.logging.info(
            f"prediction notifier enabled (topic: {self._topic_path})"
        )

    @staticmethod
    def from_env() -> typing.Optional["PredictionNotifier"]:
        """Build from env vars; returns None (disabled) unless both are set."""
        project = os.getenv("PUBSUB_PROJECT")
        topic = os.getenv("PUBSUB_TOPIC_PREDICTIONS")
        if not project or not topic:
            return None
        return PredictionNotifier(project=project, topic=topic)

    def publish_stored(
        self,
        validator_request_id: int,
        simulation_input: SimulationInput,
    ) -> None:
        """Fire-and-forget publish; never raises."""
        try:
            body = json.dumps(
                {
                    "validator_request_id": int(validator_request_id),
                    "asset": simulation_input.asset,
                    "time_length": simulation_input.time_length,
                    "start_time": simulation_input.start_time,
                }
            ).encode("utf-8")
            # publish() batches asynchronously and returns a future; we do
            # not block the cycle on the result. Failures surface via the
            # future's callback below.
            future = self._publisher.publish(
                self._topic_path,
                body,
                asset=simulation_input.asset,
                time_length=str(simulation_input.time_length),
            )
            future.add_done_callback(self._log_publish_failure)
        except Exception as e:
            bt.logging.warning(f"prediction notify failed (ignored): {e}")

    @staticmethod
    def _log_publish_failure(future: typing.Any) -> None:
        try:
            future.result()
        except Exception as e:
            bt.logging.warning(f"prediction notify failed (ignored): {e}")

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from neurons.validator import Validator


def _fake_validator():
    return SimpleNamespace(
        last_metagraph_refresh_at=None,
        miner_uids=[1, 2],
        cycle_name="high_frequency",
        miner_data_handler=None,
    )


class TestRefreshMetagraph(unittest.TestCase):
    def test_sync_failure_keeps_previous_miner_set(self):
        # A chain sync failure (e.g. endpoint rate limit) must not
        # propagate — the boot call and the scoring loop have no other
        # safety net — and must not stamp the timer, so the next cycle
        # retries.
        fake = _fake_validator()
        with patch(
            "neurons.validator."
            "get_available_miners_and_update_metagraph_history",
            side_effect=ConnectionError("server rejected: HTTP 429"),
        ):
            Validator.refresh_metagraph(fake, 15)
        self.assertEqual(fake.miner_uids, [1, 2])
        self.assertIsNone(fake.last_metagraph_refresh_at)

    def test_successful_sync_stamps_timer(self):
        fake = _fake_validator()
        with patch(
            "neurons.validator."
            "get_available_miners_and_update_metagraph_history",
            return_value=[3, 4],
        ):
            Validator.refresh_metagraph(fake, 15)
        self.assertEqual(fake.miner_uids, [3, 4])
        self.assertIsNotNone(fake.last_metagraph_refresh_at)


if __name__ == "__main__":
    unittest.main()

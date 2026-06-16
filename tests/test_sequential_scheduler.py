import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from synth.utils.sequential_scheduler import SequentialScheduler
from synth.validator.prompt_config import (
    HIGH_FREQUENCY,
    LOW_FREQUENCY,
    PromptConfig,
)


def _config(cycle_interval_minutes: int) -> PromptConfig:
    return PromptConfig(
        asset_list=["BTC"],
        label="test",
        time_length=86400,
        time_increment=300,
        cycle_interval_minutes=cycle_interval_minutes,
        timeout_extra_seconds=60,
        scoring_intervals={},
        window_days=10,
        softmax_beta=-0.1,
        smoothed_score_coefficient=0.5,
    )


class TestSelectDelay(unittest.TestCase):
    def test_first_run_aligns_to_next_minute(self):
        # Mid-minute: 30 s to the next minute boundary, regardless of interval.
        now = datetime(2026, 5, 14, 14, 1, 30, tzinfo=timezone.utc)
        with patch(
            "synth.utils.sequential_scheduler.get_current_time",
            return_value=now,
        ):
            delay = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=_config(cycle_interval_minutes=5),
                first_run=True,
            )
        self.assertEqual(delay, 30)

    def test_interval_is_independent_of_asset_count(self):
        # Cycle start aligned to the minute → delay should equal interval * 60.
        now = datetime(2026, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
        cfg = _config(cycle_interval_minutes=5)
        with patch(
            "synth.utils.sequential_scheduler.get_current_time",
            return_value=now,
        ):
            delay = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=cfg,
                first_run=False,
            )
        self.assertEqual(delay, 300)

    def test_single_asset_does_not_inflate_delay(self):
        # Regression for the PR #261 narrowing case: BTC-only must still be 5 min,
        # not 60 min.
        now = datetime(2026, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
        cfg = _config(cycle_interval_minutes=5)
        with patch(
            "synth.utils.sequential_scheduler.get_current_time",
            return_value=now,
        ):
            delay = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=cfg,
                first_run=False,
            )
        self.assertEqual(delay, 300)

    def test_high_frequency_defaults(self):
        now = datetime(2026, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
        with patch(
            "synth.utils.sequential_scheduler.get_current_time",
            return_value=now,
        ):
            delay = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=HIGH_FREQUENCY,
                first_run=False,
            )
        self.assertEqual(delay, HIGH_FREQUENCY.cycle_interval_minutes * 60)

    def test_low_frequency_defaults(self):
        now = datetime(2026, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
        with patch(
            "synth.utils.sequential_scheduler.get_current_time",
            return_value=now,
        ):
            delay = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=LOW_FREQUENCY,
                first_run=False,
            )
        self.assertEqual(delay, LOW_FREQUENCY.cycle_interval_minutes * 60)

    def test_overrun_falls_back_to_next_minute(self):
        # cycle_start_time is in the past; "now" is past the computed next_cycle
        # → delay would be non-positive, fallback to next minute boundary.
        cycle_start_time = datetime(2026, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 14, 14, 10, 15, tzinfo=timezone.utc)
        with (
            patch(
                "synth.utils.sequential_scheduler.get_current_time",
                return_value=now,
            ),
            patch(
                "synth.utils.sequential_scheduler.bt.logging.warning"
            ) as warn_mock,
        ):
            delay = SequentialScheduler.select_delay(
                cycle_start_time=cycle_start_time,
                prompt_config=_config(cycle_interval_minutes=5),
                first_run=False,
            )
        self.assertEqual(delay, 45)
        warn_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

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


def _grid_config(cycle_interval_minutes: int, offset: int) -> PromptConfig:
    cfg = _config(cycle_interval_minutes=cycle_interval_minutes)
    cfg.cycle_offset_minutes = offset
    return cfg


class TestSelectGridDelay(unittest.TestCase):
    # 2026-05-14 14:00 UTC is minute 29646120 since epoch: even, and ≡ 0 mod 5.

    def test_lanes_interleave(self):
        # interval 2 at 14:00:30 → lane 0 fires at 14:02, lane 1 at 14:01.
        # Together the two lanes cover every minute.
        now = datetime(2026, 5, 14, 14, 0, 30, tzinfo=timezone.utc)
        with patch(
            "synth.utils.sequential_scheduler.get_current_time",
            return_value=now,
        ):
            delay_lane_0 = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=_grid_config(cycle_interval_minutes=2, offset=0),
                first_run=True,
            )
            delay_lane_1 = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=_grid_config(cycle_interval_minutes=2, offset=1),
                first_run=True,
            )
        self.assertEqual(delay_lane_1, 30)
        self.assertEqual(delay_lane_0, 90)

    def test_steady_state_keeps_lane(self):
        # Previous cycle fired on its lane slot (14:00, lane 0 mod 2) and
        # finished 70 s in → next fire is 14:02, no overrun warning.
        cycle_start_time = datetime(2026, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 14, 14, 1, 10, tzinfo=timezone.utc)
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
                prompt_config=_grid_config(cycle_interval_minutes=2, offset=0),
                first_run=False,
            )
        self.assertEqual(delay, 50)
        warn_mock.assert_not_called()

    def test_overrun_relocks_to_own_lane(self):
        # Cycle started at 14:00 (lane 0 mod 2) but ran 130 s — past its
        # 14:02 slot. It must skip to 14:04 (its own lane), not drift to
        # the odd-minute lane, and warn about the missed slot.
        cycle_start_time = datetime(2026, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 14, 14, 2, 10, tzinfo=timezone.utc)
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
                prompt_config=_grid_config(cycle_interval_minutes=2, offset=0),
                first_run=False,
            )
        self.assertEqual(delay, 110)
        warn_mock.assert_called_once()

    def test_offset_none_keeps_relative_schedule(self):
        # Guard: default configs (offset unset) must not take the grid path —
        # delay stays relative to the previous cycle, not the wall clock.
        now = datetime(2026, 5, 14, 14, 1, 0, tzinfo=timezone.utc)
        with patch(
            "synth.utils.sequential_scheduler.get_current_time",
            return_value=now,
        ):
            delay = SequentialScheduler.select_delay(
                cycle_start_time=now,
                prompt_config=_config(cycle_interval_minutes=5),
                first_run=False,
            )
        self.assertEqual(delay, 300)


if __name__ == "__main__":
    unittest.main()

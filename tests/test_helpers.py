import unittest
from datetime import datetime, timedelta, timezone


from synth.utils.helpers import (
    convert_prices_to_time_format,
    get_intersecting_arrays,
    metagraph_refresh_due,
    round_time_to_minutes,
    from_iso_to_unix_time,
    get_current_time,
    round_to_8_significant_digits,
)


class TestHelpers(unittest.TestCase):
    def test_get_current_time(self):
        current_time = get_current_time()
        print("current_time", current_time)

    def test_round_to_8_significant_digits(self):
        assert (
            round_to_8_significant_digits(123456.789) == 123456.79
        )  # Rounds to 8 significant digits
        assert (
            round_to_8_significant_digits(123) == 123
        )  # Stays the same, already 3 significant digits
        assert round_to_8_significant_digits(0.000123456789) == 0.00012345679
        assert round_to_8_significant_digits(0.0) == 0.0

    def test_convert_prices_to_time_format(self):
        prices = [[45.67, 56.78, 34.89, 62.15]]
        start_time = "2024-11-19T23:00:00"
        time_increment = 300  # 5 minutes in seconds

        formatted_data = convert_prices_to_time_format(
            prices, start_time, time_increment
        )

        self.assertEqual(
            formatted_data,
            (1732057200, 300, [45.67, 56.78, 34.89, 62.15]),
        )

    def test_get_intersecting_arrays(self):
        array1 = [
            {"time": "2024-11-20T00:00:00", "price": 45.67},
            {"time": "2024-11-20T00:05:00", "price": 56.78},
            {"time": "2024-11-20T00:10:00", "price": 34.89},
        ]

        array2 = [
            {"time": "2024-11-20T00:05:00", "price": 56.78},
            {"time": "2024-11-20T00:10:00", "price": 62.15},
            {"time": "2024-11-20T00:15:00", "price": 75.20},
        ]

        intersecting_array1, intersecting_array2 = get_intersecting_arrays(
            array1, array2
        )

        self.assertEqual(
            intersecting_array1,
            [
                {"time": "2024-11-20T00:05:00", "price": 56.78},
                {"time": "2024-11-20T00:10:00", "price": 34.89},
            ],
        )

        self.assertEqual(
            intersecting_array2,
            [
                {"time": "2024-11-20T00:05:00", "price": 56.78},
                {"time": "2024-11-20T00:10:00", "price": 62.15},
            ],
        )

    def test_round_time_to_minutes(self):
        time_increment = 0

        self.assertEqual(
            round_time_to_minutes(
                datetime.fromisoformat("2024-11-25T19:01:59.940515"),
                time_increment,
            ).isoformat(),
            "2024-11-25T19:02:00",
        )
        self.assertEqual(
            round_time_to_minutes(
                datetime.fromisoformat("2024-11-25T19:03:01.940515"),
                time_increment,
            ).isoformat(),
            "2024-11-25T19:04:00",
        )

    def test_round_time_add_extra(self):
        # add three extra minutes
        dt_str_1 = "2024-11-25T19:01:59.940515"
        result_1 = round_time_to_minutes(
            datetime.fromisoformat(dt_str_1), 60 * 3
        )
        self.assertEqual(result_1.isoformat(), "2024-11-25T19:05:00")

        # add one extra minute
        dt_str_2 = "2024-11-25T19:03:59.940515"
        result_2 = round_time_to_minutes(datetime.fromisoformat(dt_str_2), 60)
        self.assertEqual(result_2.isoformat(), "2024-11-25T19:05:00")

    def test_round_time_add_extra_seconds(self):
        dt_str_1 = "2024-11-25T19:11:46.940515"
        result_1 = round_time_to_minutes(datetime.fromisoformat(dt_str_1), 10)
        self.assertEqual(result_1.isoformat(), "2024-11-25T19:12:10")

        dt_str_2 = "2024-11-25T19:03:09.659353"
        result_2 = round_time_to_minutes(datetime.fromisoformat(dt_str_2), 120)
        self.assertEqual(result_2.isoformat(), "2024-11-25T19:06:00")

    def test_round_time_to_two_minutes(self):
        extra_seconds = 60

        dt_str_1 = "2024-11-25T19:01:59.940515"
        result_1 = round_time_to_minutes(
            datetime.fromisoformat(dt_str_1), extra_seconds
        )
        self.assertEqual(result_1.isoformat(), "2024-11-25T19:03:00")

        dt_str_2 = "2024-11-25T19:03:59.940515"
        result_2 = round_time_to_minutes(
            datetime.fromisoformat(dt_str_2), extra_seconds
        )
        self.assertEqual(result_2.isoformat(), "2024-11-25T19:05:00")

    def test_from_iso_to_unix_time(self):
        iso_time = "2024-11-25T16:20:00"

        unix_time = from_iso_to_unix_time(iso_time)

        self.assertEqual(unix_time, 1732551600)

        self.assertEqual(
            from_iso_to_unix_time("2025-08-05T14:56:00+00:00"), 1754405760
        )

    def test_metagraph_refresh_due(self):
        now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)

        # never refreshed -> due
        self.assertTrue(metagraph_refresh_due(None, now, 15))

        # refreshed less than the interval ago -> not due
        self.assertFalse(
            metagraph_refresh_due(now - timedelta(minutes=14), now, 15)
        )

        # exactly the interval ago (or more) -> due
        self.assertTrue(
            metagraph_refresh_due(now - timedelta(minutes=15), now, 15)
        )
        self.assertTrue(
            metagraph_refresh_due(now - timedelta(hours=2), now, 60)
        )

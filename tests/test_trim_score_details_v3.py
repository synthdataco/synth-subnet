import unittest

from verify.trim_score_details_v3 import trim_crps_data


class TestTrimCrpsData(unittest.TestCase):
    def test_drops_increment_rows_keeps_totals(self):
        crps_data = [
            {"Interval": "5min", "Increment": 1, "CRPS": 1.0},
            {"Interval": "5min", "Increment": 2, "CRPS": 2.0},
            {"Interval": "5min", "Increment": "Total", "CRPS": 3.0},
            {"Interval": "30min", "Increment": 1, "CRPS": 4.0},
            {"Interval": "30min", "Increment": "Total", "CRPS": 4.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 7.0},
        ]

        result = trim_crps_data(crps_data)

        self.assertEqual(
            result,
            [
                {"Interval": "5min", "Increment": "Total", "CRPS": 3.0},
                {"Interval": "30min", "Increment": "Total", "CRPS": 4.0},
                {"Interval": "Overall", "Increment": "Total", "CRPS": 7.0},
            ],
        )

    def test_reconstructs_gap_total_before_overall(self):
        crps_data = [
            {"Interval": "1min", "Increment": 1, "CRPS": 1.0},
            {"Interval": "1min", "Increment": "Total", "CRPS": 1.0},
            {"Interval": "0_5min_gaps", "Increment": 1, "CRPS": 2.0},
            {"Interval": "0_5min_gaps", "Increment": "Total", "CRPS": 2.0},
            {"Interval": "0_10min_gaps", "Increment": 1, "CRPS": 3.0},
            {"Interval": "0_10min_gaps", "Increment": "Total", "CRPS": 3.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 6.0},
        ]

        result = trim_crps_data(crps_data)

        self.assertEqual(
            result,
            [
                {"Interval": "1min", "Increment": "Total", "CRPS": 1.0},
                {"Interval": "0_5min_gaps", "Increment": "Total", "CRPS": 2.0},
                {
                    "Interval": "0_10min_gaps",
                    "Increment": "Total",
                    "CRPS": 3.0,
                },
                # gap total = 2.0 + 3.0, inserted right before Overall
                {"Interval": "Gaps", "Increment": "Total", "CRPS": 5.0},
                {"Interval": "Overall", "Increment": "Total", "CRPS": 6.0},
            ],
        )

    def test_no_gaps_adds_no_aggregate(self):
        crps_data = [
            {"Interval": "5min", "Increment": 1, "CRPS": 1.0},
            {"Interval": "5min", "Increment": "Total", "CRPS": 1.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 1.0},
        ]

        result = trim_crps_data(crps_data)

        self.assertNotIn("Gaps", [d["Interval"] for d in result])

    def test_idempotent(self):
        crps_data = [
            {"Interval": "0_5min_gaps", "Increment": 1, "CRPS": 2.0},
            {"Interval": "0_5min_gaps", "Increment": "Total", "CRPS": 2.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 2.0},
        ]

        once = trim_crps_data(crps_data)
        twice = trim_crps_data(once)

        self.assertEqual(once, twice)

    def test_error_payload_left_untouched(self):
        crps_data = [{"error": "Zero price encountered in simulation runs"}]
        self.assertEqual(trim_crps_data(crps_data), crps_data)


if __name__ == "__main__":
    unittest.main()

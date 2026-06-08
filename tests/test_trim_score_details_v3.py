import unittest

from verify.trim_score_details_v3 import trim_crps_data


class TestTrimCrpsData(unittest.TestCase):
    def test_drops_increment_rows_keeps_regular_totals(self):
        crps_data = [
            {"Interval": "5min", "Increment": 1, "CRPS": 1.0},
            {"Interval": "5min", "Increment": 2, "CRPS": 2.0},
            {"Interval": "5min", "Increment": "Total", "CRPS": 3.0},
            {"Interval": "30min", "Increment": 1, "CRPS": 4.0},
            {"Interval": "30min", "Increment": "Total", "CRPS": 4.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 7.0},
        ]

        self.assertEqual(
            trim_crps_data(crps_data),
            [
                {"Interval": "5min", "Increment": "Total", "CRPS": 3.0},
                {"Interval": "30min", "Increment": "Total", "CRPS": 4.0},
                {"Interval": "Overall", "Increment": "Total", "CRPS": 7.0},
            ],
        )

    def test_drops_individual_gap_totals_keeps_aggregate(self):
        # verbose row: per-increment rows + per-interval gap totals, no
        # existing Gaps aggregate. The individual *_gaps totals are dropped
        # and replaced by a single Gaps aggregate (2.0 + 3.0).
        crps_data = [
            {"Interval": "1min", "Increment": 1, "CRPS": 1.0},
            {"Interval": "1min", "Increment": "Total", "CRPS": 1.0},
            {"Interval": "0_5min_gaps", "Increment": 1, "CRPS": 2.0},
            {"Interval": "0_5min_gaps", "Increment": "Total", "CRPS": 2.0},
            {"Interval": "0_10min_gaps", "Increment": "Total", "CRPS": 3.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 6.0},
        ]

        self.assertEqual(
            trim_crps_data(crps_data),
            [
                {"Interval": "1min", "Increment": "Total", "CRPS": 1.0},
                {"Interval": "Gaps", "Increment": "Total", "CRPS": 5.0},
                {"Interval": "Overall", "Increment": "Total", "CRPS": 6.0},
            ],
        )

    def test_retrims_previous_version_shape(self):
        # Rows trimmed by the previous version still carry the individual
        # *_gaps totals alongside a Gaps aggregate; both collapse to one Gaps.
        crps_data = [
            {"Interval": "1min", "Increment": "Total", "CRPS": 1.0},
            {"Interval": "0_5min_gaps", "Increment": "Total", "CRPS": 2.0},
            {"Interval": "0_10min_gaps", "Increment": "Total", "CRPS": 3.0},
            {"Interval": "Gaps", "Increment": "Total", "CRPS": 5.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 6.0},
        ]

        self.assertEqual(
            trim_crps_data(crps_data),
            [
                {"Interval": "1min", "Increment": "Total", "CRPS": 1.0},
                {"Interval": "Gaps", "Increment": "Total", "CRPS": 5.0},
                {"Interval": "Overall", "Increment": "Total", "CRPS": 6.0},
            ],
        )

    def test_aggregate_only_row_is_unchanged(self):
        # Already in the target shape -> no-op (preserve the Gaps row), so the
        # migration can re-scan from id 0 without rewriting these rows.
        crps_data = [
            {"Interval": "1min", "Increment": "Total", "CRPS": 1.0},
            {"Interval": "Gaps", "Increment": "Total", "CRPS": 5.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 6.0},
        ]

        self.assertEqual(trim_crps_data(crps_data), crps_data)

    def test_no_gaps_adds_no_aggregate(self):
        crps_data = [
            {"Interval": "5min", "Increment": 1, "CRPS": 1.0},
            {"Interval": "5min", "Increment": "Total", "CRPS": 1.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 1.0},
        ]

        result = trim_crps_data(crps_data)

        self.assertNotIn("Gaps", [d["Interval"] for d in result])

    def test_idempotent_across_shapes(self):
        verbose = [
            {"Interval": "0_5min_gaps", "Increment": 1, "CRPS": 2.0},
            {"Interval": "0_5min_gaps", "Increment": "Total", "CRPS": 2.0},
            {"Interval": "0_10min_gaps", "Increment": "Total", "CRPS": 3.0},
            {"Interval": "Overall", "Increment": "Total", "CRPS": 5.0},
        ]

        once = trim_crps_data(verbose)
        twice = trim_crps_data(once)

        self.assertEqual(once, twice)
        self.assertEqual(
            once,
            [
                {"Interval": "Gaps", "Increment": "Total", "CRPS": 5.0},
                {"Interval": "Overall", "Increment": "Total", "CRPS": 5.0},
            ],
        )

    def test_error_payload_left_untouched(self):
        crps_data = [{"error": "Zero price encountered in simulation runs"}]
        self.assertEqual(trim_crps_data(crps_data), crps_data)


if __name__ == "__main__":
    unittest.main()

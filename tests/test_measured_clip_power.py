"""Clip power labels must come from actual valid readings, including late peaks."""
import datetime as dt
import unittest

from rasad.twopanel import _power_timeline


class MeasuredClipPowerTests(unittest.TestCase):
    def setUp(self):
        self.start = dt.datetime(2026, 11, 8, 6, tzinfo=dt.timezone.utc)

    def times(self, count):
        return [self.start + dt.timedelta(minutes=10 * i) for i in range(count)]

    def test_missing_measurements_never_create_power_values(self):
        self.assertEqual(_power_timeline(self.times(4), [], 10),
                         ([None] * 4, [None] * 4))

    def test_real_late_peak_replaces_earlier_reading_and_is_not_interpolated(self):
        times = self.times(20)
        measured, shown = _power_timeline(times, [[times[1].isoformat(), 51],
                                                [times[18].isoformat(), 164]], 10)
        self.assertEqual(measured[1], 51)
        self.assertEqual(measured[18], 164)
        self.assertIsNone(shown[0])
        self.assertEqual(shown[1:18], [51] * 17)
        self.assertEqual(shown[18:], [164, 164])

    def test_invalid_negative_nonfinite_boolean_and_malformed_rows_are_ignored(self):
        stamp = self.start.isoformat()
        rows = [[stamp, value] for value in (None, True, -1, float("inf"), float("nan"), "bad")]
        rows += [None, "bad", [stamp], ["not a time", 100], {"t": stamp, "frp": 200}]
        self.assertEqual(_power_timeline(self.times(2), rows, 10), ([None, None], [None, None]))

    def test_zero_and_fractional_real_measurements_remain_valid(self):
        times = self.times(3)
        self.assertEqual(_power_timeline(times, [[times[0], 0], [times[1], 0.3]], 10),
                         ([0, 0.3, None], [0, 0.3, 0.3]))

    def test_highest_actual_reading_wins_when_two_sources_share_one_slot(self):
        rows = [[self.start.isoformat(), 5.5], [self.start.isoformat(), 13.4]]
        self.assertEqual(_power_timeline(self.times(2), rows, 10),
                         ([13.4, None], [13.4, 13.4]))

    def test_readings_outside_frame_tolerance_do_not_appear(self):
        rows = [[(self.start - dt.timedelta(minutes=7)).isoformat(), 100]]
        self.assertEqual(_power_timeline(self.times(1), rows, 10), ([None], [None]))

    def test_readings_before_visible_fire_do_not_leak_into_later_frames(self):
        times = self.times(4)
        measured, shown = _power_timeline(times, [[times[0], 51], [times[3], 164]], 10,
                                         shown_from=2)
        self.assertEqual(measured, [51, None, None, 164])
        self.assertEqual(shown, [None, None, None, 164])

    def test_timezone_offsets_and_naive_utc_readings_are_supported(self):
        for stamp in ("2026-11-08T09:00:00+03:00", "2026-11-08T06:00:00", "2026-11-08T06:00:00Z"):
            with self.subTest(stamp=stamp):
                self.assertEqual(_power_timeline(self.times(1), [[stamp, 13]], 10), ([13], [13]))


if __name__ == "__main__":
    unittest.main()

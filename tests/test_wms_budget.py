"""Network phases must stop probing instead of waiting through an unbounded source outage."""
import datetime as dt
import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import requests

from rasad import wms as mod
from rasad.util import UTC, iso


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class WMSBudgetTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 11, 8, 12, tzinfo=UTC)

    def setUp(self):
        self.session = Mock()
        self.wms = mod.WMS("https://example.invalid/wms", self.session)
        self.clock = Clock()
        clock_patch = patch.object(mod.time, "monotonic", self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def test_nested_budgets_use_shortest_remaining_time_and_restore_after_error(self):
        with self.wms.budget(10, timeout_s=3):
            self.assertEqual(self.wms._timeout(), 3)
            with self.assertRaisesRegex(RuntimeError, "stop"):
                with self.wms.budget(2, timeout_s=8):
                    self.clock.value += 1
                    self.assertEqual(self.wms._timeout(), 1)
                    raise RuntimeError("stop")
            self.assertEqual(self.wms._timeout(), 3)
            self.clock.value += 8
            self.assertEqual(self.wms._timeout(), 1)
        self.assertEqual(self.wms._timeout(), 20)
        self.assertIsNone(self.wms._deadline)

    def test_expired_phase_does_not_send_map_feature_or_capability_requests(self):
        with self.wms.budget(0):
            self.assertEqual(self.wms.getmap("x", [0, 0, 1, 1], 8, 8, self.WHEN), (None, None))
            self.assertIsNone(self.wms.feature_info("x", [0, 0, 1, 1], 8, 8, 4, 4, self.WHEN))
            self.assertIsNone(self.wms.time_end("x"))
        self.session.get.assert_not_called()

    def test_live_request_timeout_cannot_exceed_remaining_phase_budget(self):
        self.session.get.return_value = Mock(status_code=503, headers={}, text="unavailable")
        with self.wms.budget(5, timeout_s=20):
            self.clock.value += 3
            self.wms.getmap("x", [0, 0, 1, 1], 8, 8, self.WHEN, timeout=60, quiet=True)
        self.assertEqual(self.session.get.call_args.kwargs["timeout"], 2)

    def test_outage_clock_discovery_is_bounded_and_preserves_last_good_time(self):
        known = self.WHEN - dt.timedelta(minutes=20)
        state = {"latest": {"x": iso(known)}}
        timeouts = []

        def unavailable(*args, **kwargs):
            timeout = kwargs["timeout"]
            timeouts.append(timeout)
            self.clock.value += timeout
            raise requests.Timeout("temporary source outage")

        self.session.get.side_effect = unavailable
        with patch.object(mod, "utcnow", return_value=self.WHEN):
            self.assertEqual(mod.latest_time(self.wms, state, "x", 10, [0, 0, 1, 1]), known)
        self.assertEqual(state["latest"]["x"], iso(known))
        self.assertLessEqual(sum(timeouts), 12)
        self.assertLessEqual(len(timeouts), 2)

    def test_unknown_clock_outage_does_not_probe_thirty_images(self):
        def unavailable(*args, **kwargs):
            self.clock.value += kwargs["timeout"]
            raise requests.Timeout("temporary source outage")

        self.session.get.side_effect = unavailable
        with patch.object(mod, "utcnow", return_value=self.WHEN):
            self.assertIsNone(mod.latest_time(self.wms, {}, "x", 10, [0, 0, 1, 1]))
        self.assertLessEqual(self.clock.value - 100, 12)
        self.assertLessEqual(self.session.get.call_count, 2)

    def test_served_time_from_warning_is_kept_instead_of_requested_time(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (24, 24), (1, 2, 3, 255)).save(buffer, "PNG")
        actual = self.WHEN - dt.timedelta(minutes=10)
        self.session.get.return_value = Mock(status_code=200, content=buffer.getvalue(), headers={
            "Content-Type": "image/png", "Warning": "nearest time=" + iso(actual)})
        state = {}
        with patch.object(mod, "utcnow", return_value=self.WHEN):
            self.assertEqual(mod.latest_time(self.wms, state, "x", 10, [0, 0, 1, 1]), actual)
        self.assertEqual(state["latest"]["x"], iso(actual))
        self.session.get.assert_called_once()
        self.assertEqual(self.session.get.call_args.kwargs["params"]["REQUEST"], "GetMap")


if __name__ == "__main__":
    unittest.main()

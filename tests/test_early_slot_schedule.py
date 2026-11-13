"""Fresh source slots can be tried immediately without marking missing images done."""
import copy
import datetime as dt
import unittest
from unittest.mock import Mock, patch

from rasad import engine, seawatch
from rasad.util import UTC, iso


class EarlySlotScheduleTests(unittest.TestCase):
    def test_missing_latest_slot_does_not_block_available_older_slot(self):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"].update(boxes=[{"id": "test", "bbox": [55, 25, 57, 27]}],
                               slots_per_run=1, catchup_hours=.5)
        cfg["seawatch"]["daytime"]["enabled"] = False
        now = dt.datetime(2026, 11, 13, 19, 1, tzinfo=UTC)
        missing = now.replace(minute=0)
        available = missing - dt.timedelta(minutes=10)
        avail = Mock()
        avail.has.side_effect = lambda layer, when, probe: when != missing
        state = {}
        with patch.object(seawatch, "Pictures"), \
                patch.object(seawatch, "_night", return_value=[True]), \
                patch.object(seawatch, "find_spots", return_value=([], {})) as finder:
            seawatch.watch(cfg, Mock(), state, now, avail=avail)
        self.assertEqual(cfg["seawatch"]["delay_min"], 0)
        self.assertEqual(avail.has.call_args_list[0].args[1], missing)
        finder.assert_called_once()
        self.assertEqual(finder.call_args.args[3], available)
        self.assertNotIn("test|" + iso(missing), state["seawatch"])
        self.assertIn("test|" + iso(available), state["seawatch"])

    def test_available_latest_slot_is_checked_without_settling_delay(self):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"].update(boxes=[{"id": "test", "bbox": [55, 25, 57, 27]}],
                               slots_per_run=1, catchup_hours=0)
        cfg["seawatch"]["daytime"]["enabled"] = False
        now = dt.datetime(2026, 11, 13, 19, 1, tzinfo=UTC)
        latest = now.replace(minute=0)
        avail = Mock()
        avail.has.return_value = True
        with patch.object(seawatch, "Pictures"), \
                patch.object(seawatch, "_night", return_value=[True]), \
                patch.object(seawatch, "find_spots", return_value=([], {})) as finder:
            seawatch.watch(cfg, Mock(), {}, now, avail=avail)
        self.assertEqual(finder.call_args.args[3], latest)


if __name__ == "__main__":
    unittest.main()

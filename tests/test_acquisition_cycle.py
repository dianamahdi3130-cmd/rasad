"""A complete scan associates whole NASA passes independently of CSV row order."""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rasad import engine
from rasad.brain import Memory
from rasad.util import UTC, iso, load_json, save_json


class AcquisitionCycleTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 8, 18, tzinfo=UTC)
    OLD_ID = "20261108-0841_26.103_55.884"

    @staticmethod
    def observations():
        # Original public NOAA-20 and Terra CSV pixels captured for this case.
        # The separate Aqua acquisition at 12:06 is deliberately not part of it.
        passes = [
            ("0841", "VIIRS_NOAA20_NRT", "viirs", "N20", False, [
                (26.10340, 55.88392, 13.30, .53, .67, "nominal"),
                (26.10939, 55.88209, 13.30, .53, .67, "high"),
            ]),
            ("1021", "VIIRS_NOAA20_NRT", "viirs", "N20", False, [
                (26.10187, 55.90488, 35.24, .54, .67, "nominal"),
                (26.10148, 55.89945, 61.07, .53, .67, "nominal"),
                (26.09984, 55.90360, 26.37, .54, .68, "high"),
                (26.09948, 55.89817, 26.37, .54, .67, "nominal"),
            ]),
            ("1702", "MODIS_NRT", "modis", "T", True, [
                (26.08609, 55.93579, 31.71, 2.15, 1.43, "89"),
                (26.08411, 55.91482, 47.42, 2.15, 1.42, "100"),
                (26.08757, 55.93005, 50.59, 2.15, 1.43, "100"),
                (26.08573, 55.90906, 21.34, 2.15, 1.42, "76"),
            ]),
        ]
        rows = []
        for clock, src, family, satellite, night, points in passes:
            for lat, lon, frp, scan, track, confidence in points:
                rows.append({"src": src, "family": family, "satellite": satellite,
                             "kind": "polar", "t": f"2026-11-08T{clock[:2]}:{clock[2:]}:00Z",
                             "lat": lat, "lon": lon, "frp": frp, "scan": scan,
                             "track": track, "conf": confidence, "night": night})
        return rows

    def cycle(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = copy.deepcopy(engine.DEFAULTS)
            cfg.update(_state_dir=root / "state", _site_dir=root / "site",
                       _geodata_dir=root / "geo")
            cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 25, 57, 27]}]
            cfg["seawatch"].update(enabled=False, look_back=False)
            cfg["eumetview"]["fire_layers"] = []
            cfg["clip"]["enabled"] = False
            cfg["firms"]["map_key"] = "offline-test-key"
            cfg["alerts"]["min_verdict"] = "يحتاج تأكيداً"
            cfg["telegram"].update(bot_token="offline-token", chat_id="offline-chat")
            save_json(cfg["_state_dir"] / "state.json", {
                "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                "sea_check": engine.SEA_CHECK, "bootstrapped": iso(self.NOW),
                "hello_sent": iso(self.NOW)})
            memory = Memory({})
            memory.record(30, 48, "2026-11-07", 10)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            session = Mock()
            for method in ("get", "post", "put"):
                getattr(session, method).side_effect = AssertionError("No network in acquisition tests")
            geo = Mock(version="offline-map")
            geo.place_kind.return_value = "sea"
            geo.facility.return_value = None
            geo.describe.return_value = ("في البحر", "الإمارات")
            deliver = Mock(return_value=1144)
            forbidden = Mock(side_effect=AssertionError("Unexpected external source or direct Telegram request"))
            with patch.multiple(engine, utcnow=Mock(return_value=self.NOW),
                                make_session=Mock(return_value=session), WMS=Mock(),
                                Geo=Mock(return_value=geo), Availability=Mock(),
                                firms_fetch=Mock(return_value=copy.deepcopy(rows)),
                                latest_time=forbidden, detect_geo_layer=forbidden,
                                deliver=deliver, tg_send=forbidden):
                state = engine.run_cycle(cfg, send=True)
            session.get.assert_not_called()
            session.post.assert_not_called()
            forbidden.assert_not_called()
            self.assertEqual(len(state.incidents), 2)
            morning = next(inc for inc in state.incidents if inc["first"].endswith("08:41:00Z"))
            evening = next(inc for inc in state.incidents if inc["first"].endswith("17:02:00Z"))
            self.assertEqual(morning["id"], self.OLD_ID)
            self.assertEqual((morning["last"], len(morning["polar"]), morning["max_frp"]),
                             ("2026-11-08T10:21:00Z", 6, 61.07))
            self.assertEqual(morning["families"], ["viirs"])
            self.assertEqual(morning["verdict"], "مرجّح")
            self.assertNotIn("modis", {point["family"] for point in morning["polar"]})
            self.assertEqual((len(evening["polar"]), evening["max_frp"]), (4, 50.59))
            self.assertTrue(evening["association_uncertain"])
            self.assertEqual(evening["association"]["previous_id"], morning["id"])
            self.assertEqual(evening["verdict"], "يحتاج تأكيداً")
            self.assertIsNone(evening["alerted_verdict"])
            # Lowering min_verdict to zero must not announce this uncertain
            # evening acquisition as a new fire or an increase of the old one.
            deliver.assert_called_once()
            self.assertEqual(morning["tg_msg"], 1144)
            self.assertAlmostEqual(morning["alerted_max_frp"], 61.07, delta=.05)
            self.assertIn("61 MW", deliver.call_args.args[2])
            self.assertNotIn("ارتفاع", deliver.call_args.args[2])
            published = load_json(cfg["_site_dir"] / "data" / "incidents.json", {})["items"]
            self.assertEqual({inc["max_frp"] for inc in published}, {61.07, 50.59})
            return tuple((inc["id"], inc["first"], inc["last"], inc["max_frp"],
                          len(inc["polar"]), bool(inc.get("association_uncertain")))
                         for inc in sorted(state.incidents, key=lambda inc: inc["first"]))

    def test_actual_passes_keep_morning_and_uncertain_evening_acquisitions_separate(self):
        self.cycle(self.observations())

    def test_reversed_and_interleaved_csv_rows_cannot_change_incident_assignment(self):
        rows = self.observations()
        expected = self.cycle(rows)
        for ordered in (list(reversed(rows)), rows[::2] + rows[1::2],
                        rows[6:] + rows[2:6][::-1] + rows[:2][::-1]):
            with self.subTest(order=[(row["t"], row["frp"]) for row in ordered]):
                self.assertEqual(self.cycle(ordered), expected)


if __name__ == "__main__":
    unittest.main()

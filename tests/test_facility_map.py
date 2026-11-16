"""Which map objects become facilities: oil and gas sites drawn only as named industrial land."""
import re
import unittest

from rasad import geodb


class NamedIndustrialLand(unittest.TestCase):
    # 15 Nov 2026: a fire at this place (25.61 N, 48.87 E) was confirmed and never posted, because
    # the tool knew no facility there. OpenStreetMap holds it exactly like this:
    JUDAH = {"landuse": "industrial", "name": "محطة ضخ أرامكو السعودية 1"}

    def asked_for(self, tags):
        """The two conditions the facility query adds for industrial land."""
        names, owners = (re.compile(pattern, re.I) for pattern in (geodb.OIL_NAMES, geodb.OIL_OWNERS))
        return tags.get("landuse") == "industrial" and bool(
            names.search(tags.get("name", "")) or owners.search(tags.get("operator", "")))

    def test_the_pumping_station_south_of_judah_is_a_facility(self):
        self.assertTrue(self.asked_for(self.JUDAH))
        category = geodb.facility_category(self.JUDAH)
        self.assertEqual(category, ("محطة ضخ نفط وغاز", "pumping"))
        self.assertEqual(geodb.facility_label(geodb.osm_name(self.JUDAH), category[0]), "محطة ضخ أرامكو السعودية 1")
        # the industrial land of the box first (an exact tag), then only its names are matched
        self.assertIn('nwr["landuse"="industrial"]({b})->.ind;', geodb.OIL_QUERY)
        self.assertIn('nwr.ind["name"~"', geodb.OIL_QUERY)
        self.assertIn('nwr.ind["operator"~"', geodb.OIL_QUERY)
        self.assertNotIn("{names}", geodb.OIL_QUERY)
        self.assertNotIn("{owners}", geodb.OIL_QUERY)
        self.assertEqual(geodb.OIL_QUERY.count("{b}"), 1)
        self.assertNotIn('"name"~', geodb.FACILITY_QUERY, "no name pattern over a whole tile (it timed out)")
        self.assertEqual(geodb.FACILITY_QUERY.count("{b}"), 9, "every line of the query still takes the box")
        self.assertGreaterEqual(geodb.GEO_VERSION, 3, "the map is built again with the wider question")

    def test_other_oil_sites_drawn_as_industrial_land(self):
        cases = [
            ({"landuse": "industrial", "name": "Pump Station 9", "operator": "Saudi Aramco"}, ("محطة ضخ نفط وغاز", "pumping")),
            ({"landuse": "industrial", "operator": "Saudi Aramco"}, ("منشأة نفطية", "oil")),
            ({"landuse": "industrial", "name": "Shaybah GOSP-2"}, ("منشأة نفطية", "oil")),
            ({"landuse": "industrial", "name": "حقل نفط الرميلة"}, ("منشأة نفطية", "oil")),
            ({"landuse": "industrial", "name": "معمل غاز الفاضلي"}, ("منشأة غاز", "gas")),
            ({"landuse": "industrial", "name": "مصفاة رأس تنورة"}, ("مصفاة", "refinery")),
            ({"landuse": "industrial", "name": "SABIC Jubail"}, ("مصنع بتروكيماويات", "petrochem")),
        ]
        for tags, expected in cases:
            with self.subTest(tags=tags):
                self.assertTrue(self.asked_for(tags))
                self.assertEqual(geodb.facility_category(tags), expected)

    def test_what_is_not_an_energy_site_stays_out(self):
        self.assertFalse(self.asked_for({"landuse": "industrial", "name": "مصنع الأثاث الحديث"}), "plain industry is not asked for")
        self.assertFalse(self.asked_for({"landuse": "industrial"}))
        # asked for because of a word in the name, but turned down
        self.assertIsNone(geodb.facility_category({"landuse": "industrial", "name": "محطة ضخ مياه الصرف الصحي"}))
        self.assertIsNone(geodb.facility_category({"landuse": "industrial", "name": "Water Pumping Station 4"}))
        self.assertIsNone(geodb.facility_category({"landuse": "industrial", "name": "مصنع زيوت نباتية"}))
        # what was a facility before stays one, with the same words
        self.assertEqual(geodb.facility_category({"man_made": "pumping_station", "substance": "oil"}), ("محطة ضخ نفط وغاز", "pumping"))
        self.assertEqual(geodb.facility_category({"industrial": "oil"}), ("منشأة نفطية", "oil"))
        self.assertEqual(geodb.facility_category({"man_made": "works", "product": "cement"}), ("منشأة صناعية", "industrial"))


class HandKeptFacilities(unittest.TestCase):
    """config: geodata.extra_facilities. They count at once, before the map is built again."""

    def geo(self, extra):
        import copy
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock

        from rasad import engine
        from rasad.util import save_json
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["_geodata_dir"] = Path(folder.name)
        cfg["geodata"]["extra_facilities"] = extra
        save_json(Path(folder.name) / "facilities.json",
                  {"built": "2026-11-03T06:29:09Z", "version": 2, "items": [[25.53049, 49.2103, "", "منشأة نفطية", "oil", ""]]})
        save_json(Path(folder.name) / "places.json", {"built": "2026-11-03T06:29:09Z", "items": []})
        return geodb.Geo(cfg, Mock(), Mock(), {})

    STATION = {"lat": 25.61143, "lon": 48.88608, "name": "محطة ضخ أرامكو السعودية 1", "category": "محطة ضخ نفط وغاز", "code": "pumping"}

    def test_the_station_is_found_from_the_fire_of_15_november(self):
        before, after = self.geo([]), self.geo([self.STATION])
        self.assertIsNone(before.facility(25.6093, 48.874), "the built map alone: nothing within 30 km")
        found = after.facility(25.6093, 48.874)
        self.assertEqual((found["label"], found["code"]), ("محطة ضخ أرامكو السعودية 1", "pumping"))
        self.assertLess(found["km"], 2)
        self.assertTrue(after.facility_within(25.6093, 48.874, 2))
        self.assertNotEqual(before.version, after.version, "recent incidents are described again when the list changes")
        self.assertEqual(after.version, self.geo([self.STATION]).version, "and not when it does not")

    def test_an_entry_without_a_place_is_left_out(self):
        geo = self.geo([{"name": "بلا موقع"}, {"lat": "x", "lon": 1}, {"lat": 95, "lon": 10}, {"lat": 24.0, "lon": 50.0}])
        self.assertEqual(len(geo.fac.items), 2)
        self.assertEqual(geo.fac.items[-1][2:5], ["", "منشأة نفطية", "oil"])

    def test_the_shipped_config_holds_the_station(self):
        from pathlib import Path

        from rasad import engine
        cfg = engine.load_config(Path(__file__).resolve().parents[1] / "config.yaml", "/tmp/unused-state", "/tmp/unused-site")
        names = [item[2] for item in geodb.extra_facilities(cfg)]
        self.assertIn("محطة ضخ أرامكو السعودية 1", names)
        self.assertTrue(cfg["geodata"]["overpass_urls"], "the rest of the map settings are still there")


class MapRebuildInSlices(unittest.TestCase):
    """16 Nov 2026: with the oil names inside the facility query, both map servers gave up after 70 s
    on the first tile, every ten minutes for five hours: the rebuilt map never came. Each part of a tile
    is now asked and saved by itself, and no request outlives the slice the watching loop gives."""

    def setUp(self):
        import copy
        import tempfile
        from pathlib import Path

        from rasad import engine
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg["_geodata_dir"] = Path(folder.name) / "geo"
        self.cfg["regions"] = [{"id": "east", "name": "شرق السعودية", "bbox": [48.0, 25.0, 50.0, 27.0]}]
        self.cfg["geodata"].update(overpass_pause_s=0, oil_query_split=2, query_tile_deg=8)
        self.calls = []
        self.fail = set()

    def post(self, url, data, timeout):
        from unittest.mock import Mock
        query = data["data"]
        kind = "oil" if "->.ind" in query else "place" if '"place"' in query else "fac"
        self.calls.append((kind, query, timeout))
        nth = len([c for c in self.calls if c[0] == kind])       # the how-many-th request of this kind
        if (kind, nth) in self.fail:
            raise geodb.requests.ReadTimeout()
        els = []
        if kind == "oil" and nth == 2:
            els = [{"type": "way", "id": 1, "center": {"lat": 25.61143, "lon": 48.88608},
                    "tags": {"landuse": "industrial", "name": "محطة ضخ أرامكو السعودية 1"}}]
        if kind == "fac":
            els = [{"type": "node", "id": 2, "lat": 26.6, "lon": 49.9,
                    "tags": {"industrial": "refinery", "name": "Ras Tanura Refinery"}}]
        if kind == "place":
            els = [{"type": "node", "id": 3, "lat": 26.4, "lon": 50.0, "tags": {"place": "city", "name": "الدمام"}}]
        return Mock(status_code=200, json=Mock(return_value={"elements": els}))

    def build(self, budget_s=None):
        from unittest.mock import Mock
        session = Mock()
        session.post.side_effect = self.post
        return geodb.build_geodata(self.cfg, session, budget_s=budget_s)

    def test_a_tile_is_asked_in_parts_and_the_named_station_lands_in_the_map(self):
        from rasad.util import load_json
        self.assertTrue(self.build())
        kinds = [c[0] for c in self.calls]
        self.assertEqual(kinds, ["fac", "oil", "oil", "oil", "oil", "place"], "one tile: facilities, 4 boxes of oil land, places")
        fac = load_json(self.cfg["_geodata_dir"] / "facilities.json", {})
        self.assertEqual(fac["version"], geodb.GEO_VERSION)
        names = {item[2]: item[4] for item in fac["items"]}
        self.assertEqual(names.get("محطة ضخ أرامكو السعودية 1"), "pumping")
        self.assertIn("refinery", names.values())

    def test_a_failed_part_keeps_what_was_done_before_it(self):
        self.fail = {("oil", 2), ("oil", 3)}                   # the second oil box times out on both servers
        self.assertFalse(self.build(budget_s=45))
        first = [c[0] for c in self.calls]
        self.assertEqual(first, ["fac", "oil", "oil", "oil"], "both servers asked for the failed part, then stop")
        self.calls.clear()
        self.fail = set()
        self.assertTrue(self.build(budget_s=45))
        self.assertEqual([c[0] for c in self.calls], ["oil", "oil", "oil", "place"],
                         "the facilities and the first oil box are not asked again")

    def test_no_request_outlives_the_slice(self):
        self.assertTrue(self.build(budget_s=45))
        self.assertTrue(all(c[2] <= 55 for c in self.calls), [c[2] for c in self.calls])
        from rasad import engine
        self.assertEqual((engine.DEFAULTS["geodata"]["query_tile_deg"], engine.DEFAULTS["geodata"]["oil_query_split"]), (4, 1),
                         "4-degree tiles, the oil land of a tile in one request")
        from unittest.mock import Mock
        session = Mock()
        import time as clock
        self.assertIsNone(geodb.overpass(session, ["https://a", "https://b"], "q", deadline=clock.monotonic() + 10))
        session.post.assert_not_called()


class NewsByChange(unittest.TestCase):
    """A change of rules or of the map does not announce a heat source that has burnt all day."""

    def test_only_what_started_recently_is_posted_after_a_change(self):
        import datetime as dt

        from rasad import engine
        now = dt.datetime(2026, 11, 15, 21, 40, tzinfo=dt.timezone.utc)
        eight = dt.timedelta(hours=8)
        station = {"first": "2026-11-15T17:40:00Z", "last": "2026-11-15T18:40:00Z"}        # the fire the owner asked to post
        all_day = {"first": "2026-11-15T05:44:00Z", "last": "2026-11-15T21:30:00Z"}       # burning since the morning
        gone = {"first": "2026-11-15T09:00:00Z", "last": "2026-11-15T10:20:00Z"}
        self.assertFalse(engine.too_old_for_first_alert(station, now, eight, by_change=True))
        self.assertTrue(engine.too_old_for_first_alert(all_day, now, eight, by_change=True))
        self.assertFalse(engine.too_old_for_first_alert(all_day, now, eight), "by its own doing it is still news while it burns")
        self.assertTrue(engine.too_old_for_first_alert(gone, now, eight))
        self.assertTrue(engine.too_old_for_first_alert(gone, now, eight, by_change=True))


class NewsByChangeInACheck(unittest.TestCase):
    """The same in a whole check: a stored confirmed fire that was not important (far from every
    mapped facility), and a map that now holds a plant one kilometre from it."""

    def check(self, started_hours_ago, described_with, new_evidence=False):
        import copy
        import datetime as dt
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, patch

        from rasad import engine
        from rasad.brain import Memory, add_to_incident, finalize, new_incident
        from rasad.util import iso, save_json
        now = dt.datetime(2026, 11, 15, 21, 40, tzinfo=dt.timezone.utc)
        lat, lon = 30.0, 48.0
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = copy.deepcopy(engine.DEFAULTS)
            cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
            cfg["regions"] = [{"id": "test", "name": "العراق", "bbox": [47, 29, 49, 31]}]
            cfg["seawatch"].update(enabled=False, look_back=False)
            cfg["clip"]["enabled"] = cfg["satpic"]["enabled"] = False
            cfg["eumetview"]["fire_layers"] = []
            cfg["telegram"].update(bot_token="offline-test-token", chat_id="offline-test-chat")
            # seen when it started, then in five pictures in a row up to ten minutes ago
            minutes = [started_hours_ago * 60, 50, 40, 30, 20, 10]
            dets = [{"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": iso(now - dt.timedelta(minutes=m)),
                     "lat": lat, "lon": lon, "frp": 12.0} for m in minutes]
            inc = new_incident(dets[0], "new")
            for d in dets:
                add_to_incident(inc, d)
            inc.update(place="land", facility=None, where="العراق", country="العراق", regions=["test"],
                       region_name="العراق", known_days=0, area_days=0)
            finalize(inc, cfg)
            self.assertEqual((inc["verdict"], inc["priority"]), ("مؤكد", "low"), "confirmed, yet not news: no facility")
            if described_with:
                inc["_pos"] = [round(lat, 3), round(lon, 3), described_with]
            save_json(cfg["_state_dir"] / "incidents.json", [inc])
            save_json(cfg["_state_dir"] / "state.json", {
                "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                "sea_check": engine.SEA_CHECK, "hello_sent": "2026-11-15T10:00:00Z"})
            memory = Memory({})
            memory.record(31.0, 47.5, "2026-11-14", 10)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            geo = Mock(version="map-with-the-plant")
            geo.place_kind.return_value = "land"
            geo.facility.return_value = {"label": "محطة ضخ نفط", "code": "pumping", "km": 1.0}
            geo.describe.return_value = ("العراق", "العراق")
            deliver = Mock(return_value=456)
            source = Mock(side_effect=AssertionError("No source requests in these tests"))
            with patch.multiple(engine, utcnow=Mock(return_value=now), make_session=Mock(), WMS=Mock(),
                                Geo=Mock(return_value=geo), Availability=Mock(), latest_time=source,
                                detect_geo_layer=source, firms_fetch=source, deliver=deliver,
                                tg_send=Mock(side_effect=AssertionError("No direct Telegram requests"))):
                state = engine.run_cycle(cfg, send=True)
            self.assertEqual(len(state.incidents), 1)
            return state.incidents[0], deliver

    def test_the_map_learns_a_plant_beside_a_fire_that_started_an_hour_ago(self):
        inc, deliver = self.check(1, described_with="older-map")
        self.assertEqual((inc["priority"], inc["status"]), ("high", "alerted"))
        deliver.assert_called_once()
        self.assertIn("حريق قرب محطة ضخ نفط", deliver.call_args.args[2])

    def test_the_map_learns_a_plant_beside_heat_that_has_burnt_all_day(self):
        inc, deliver = self.check(12, described_with="older-map")
        self.assertEqual((inc["priority"], inc["status"]), ("high", "late"), "shown in the app, not announced")
        deliver.assert_not_called()

    def test_a_fire_whose_place_was_never_described_is_not_a_change_of_the_map(self):
        # its description was still waiting (the naming service was slow): once it has one and it
        # is beside a plant, it is news while it burns, however long ago it started
        inc, deliver = self.check(12, described_with=None)
        self.assertEqual((inc["priority"], inc["status"]), ("high", "alerted"))
        deliver.assert_called_once()


if __name__ == "__main__":
    unittest.main()

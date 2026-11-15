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
        self.assertIn('nwr["landuse"="industrial"]["name"~"', geodb.FACILITY_QUERY)
        self.assertIn('nwr["landuse"="industrial"]["operator"~"', geodb.FACILITY_QUERY)
        self.assertNotIn("{names}", geodb.FACILITY_QUERY)
        self.assertEqual(geodb.FACILITY_QUERY.count("{b}"), 11, "every line of the query still takes the box")
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


if __name__ == "__main__":
    unittest.main()

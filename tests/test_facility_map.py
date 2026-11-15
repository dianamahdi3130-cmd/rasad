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


if __name__ == "__main__":
    unittest.main()

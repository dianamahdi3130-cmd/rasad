"""A satellite acquisition must not change its incident assignment with CSV row order."""
import copy
import itertools
import unittest

from rasad import acquisitions
from rasad.brain import add_to_incident, find_incident, new_incident, verdict_of
from rasad.util import haversine_km


class AcquisitionTests(unittest.TestCase):
    @staticmethod
    def point(lat, lon, when="2026-11-08T10:21:00Z", frp=10, family="viirs", **extra):
        return {"src": "MODIS_NRT" if family == "modis" else "VIIRS_NOAA20_NRT",
                "family": family, "kind": "polar", "t": when, "lat": lat, "lon": lon,
                "frp": frp, "conf": "high", "scan": 0.54, "track": 0.68,
                "satellite": "N20", "night": False, **extra}

    def actual(self):
        # Public FIRMS records captured for the reported 26 -> 47 MW incident.
        morning = [(26.1034, 55.8839, 13.3), (26.1094, 55.8821, 13.3)]
        later = [(26.1019, 55.9049, 35.24), (26.1015, 55.8995, 61.07),
                 (26.0998, 55.9036, 26.37), (26.0995, 55.8982, 26.37)]
        evening = [(26.0861, 55.9358, 31.71), (26.0841, 55.9148, 47.42),
                   (26.0876, 55.9301, 50.59), (26.0857, 55.9091, 21.34)]
        return [[self.point(lat, lon, "2026-11-08T08:41:00Z", mw, scan=0.53, track=0.67)
                 for lat, lon, mw in morning],
                [self.point(lat, lon, frp=mw) for lat, lon, mw in later],
                [self.point(lat, lon, "2026-11-08T17:02:00Z", mw, family="modis",
                            conf="100", scan=2.15, track=1.43, satellite="T", night=True)
                 for lat, lon, mw in evening]]

    @staticmethod
    def replay(dets):
        incidents = []
        for group in acquisitions.groups(copy.deepcopy(list(dets))):
            incident = acquisitions.find_group(incidents, group, 6, 12)
            if incident is None:
                incident = new_incident(group[0], "new")
                incidents.append(incident)
            for det in group:
                add_to_incident(incident, det)
        return incidents

    def test_real_case_is_two_stable_groups_for_all_1152_pixel_orderings(self):
        original_id = "20261108-0841_26.103_55.884"
        morning, later, evening = self.actual()
        count = 0
        for passes in itertools.product(*(itertools.permutations(rows) for rows in (morning, later, evening))):
            incidents = self.replay(itertools.chain.from_iterable(passes))
            self.assertEqual(len(incidents), 2)
            first, second = incidents
            self.assertEqual((first["id"], first["max_frp"], len(first["observations"])),
                             (original_id, 61.07, 6))
            self.assertEqual(first["last"], "2026-11-08T10:21:00Z")
            self.assertFalse(first.get("association_uncertain", False))
            self.assertEqual((second["max_frp"], len(second["observations"])), (50.59, 4))
            self.assertTrue(second["association_uncertain"])
            self.assertEqual(second["association"]["previous_id"], original_id)
            self.assertEqual(verdict_of(second)[0], "يحتاج تأكيداً")
            count += 1
        self.assertEqual(count, 1152)

    def test_whole_acquisition_matches_when_its_first_csv_pixel_alone_does_not(self):
        morning, later, _ = self.actual()
        old = self.replay(morning)[0]
        individual = copy.deepcopy(later[0])
        self.assertIsNone(find_incident([old], individual, 6, 12))
        group = acquisitions.groups(later)[0]
        self.assertIs(acquisitions.find_group([old], group, 6, 12), old)
        self.assertTrue(all("_association" not in det for det in group))

    def test_same_time_but_distinct_precise_footprints_remain_separate(self):
        points = [self.point(26, 55, scan=0.375, track=0.375),
                  self.point(26, 55.03, scan=0.375, track=0.375)]
        self.assertEqual([len(group) for group in acquisitions.groups(points)], [1, 1])

    def test_source_platform_and_timestamp_each_keep_distinct_acquisitions(self):
        first = self.point(26, 55)
        points = [first, dict(first, src="VIIRS_NOAA21_NRT"),
                  dict(first, satellite="different"), dict(first, t="2026-11-08T10:22:00Z")]
        self.assertEqual(len(acquisitions.groups(points)), 4)

    def test_nonpolar_symbols_stay_singletons(self):
        first = self.point(26, 55, kind="geo", src="mtg_frp", family="mtg")
        points = [first, dict(first, lon=55.001)]
        self.assertEqual(len(acquisitions.groups(points)), 2)

    def test_large_connected_chain_is_bounded_without_losing_any_pixel(self):
        points = [self.point(26, 55 + index * 0.025, scan=4, track=4) for index in range(25)]
        groups = acquisitions.groups(points, merge_km=6)
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(map(len, groups)), 25)
        for group in groups:
            lat = sum(row["lat"] for row in group) / len(group)
            lon = sum(row["lon"] for row in group) / len(group)
            self.assertTrue(all(haversine_km(lat, lon, row["lat"], row["lon"]) <= 6.000001
                                for row in group))

    def test_date_line_acquisition_has_a_local_centroid_after_a_long_gap(self):
        rows = [self.point(0, 179.995), self.point(0, -179.995)]
        self.assertEqual(len(acquisitions.groups(rows)), 1)
        old = self.replay(rows)[0]
        newer = [dict(row, t="2026-11-08T17:02:00Z") for row in rows]
        self.assertIs(acquisitions.find_group([old], acquisitions.groups(newer)[0], 6, 12), old)

    def test_empty_input_does_not_create_an_incident(self):
        self.assertEqual(acquisitions.groups([]), [])
        self.assertIsNone(acquisitions.find_group([], [], 6, 12))


if __name__ == "__main__":
    unittest.main()

"""Unrelated past heat cannot create wide blind areas around a new marine fire."""
import copy
import datetime as dt
import json
import unittest
from types import SimpleNamespace

from rasad.brain import Memory
from rasad.thermal_history import history_rules, persistent_local_heat
from rasad.util import haversine_km


class ThermalHistoryTests(unittest.TestCase):
    DAY = dt.date(2026, 11, 14)
    LAT, LON = 25.01, 57.01

    def memory(self, *, lat=None, lon=None, days=(1, 2, 3), power=10):
        result = Memory({})
        for offset in days:
            result.record(self.LAT if lat is None else lat, self.LON if lon is None else lon,
                          (self.DAY - dt.timedelta(days=offset)).isoformat(), power)
        return result

    def known(self, memory, **settings):
        return persistent_local_heat(memory, self.LAT, self.LON, self.DAY, settings)

    def test_a_single_old_local_ship_is_not_a_stationary_heat_source(self):
        self.assertFalse(self.known(self.memory(days=(1,))))

    def test_three_prior_dates_at_one_local_cell_stay_excluded(self):
        self.assertTrue(self.known(self.memory()))

    def test_a_single_heat_observation_eight_km_away_does_not_hide_the_new_spot(self):
        memory = self.memory(lat=self.LAT + 8 / 111.32, days=(1,))
        self.assertFalse(self.known(memory))

    def test_a_recurrent_heat_source_eight_km_away_does_not_hide_the_new_spot(self):
        memory = self.memory(lat=self.LAT + 8 / 111.32)
        self.assertFalse(self.known(memory))

    def test_neighbouring_local_cell_is_excluded_only_inside_physical_radius(self):
        # The configured radius is geodesic, not the bounding square's diagonal.
        nearby = self.memory(lat=self.LAT + .02)
        farther = self.memory(lat=self.LAT + .04)
        self.assertLess(haversine_km(self.LAT, self.LON, self.LAT + .02, self.LON), 3)
        self.assertTrue(self.known(nearby))
        self.assertFalse(self.known(farther))
        self.assertFalse(self.known(nearby, known_heat_km=1))
        self.assertFalse(self.known(nearby, known_heat_exclusion_km=1))

    def test_current_future_and_older_than_thirty_days_do_not_count(self):
        memory = self.memory(days=(0, -1, 31))
        self.assertFalse(self.known(memory))
        self.assertTrue(self.known(self.memory(days=(1, 2, 30))))

    def test_three_days_in_three_separate_cells_are_not_one_persistent_source(self):
        memory = Memory({})
        for offset, latitude in ((1, self.LAT), (2, self.LAT + .02), (3, self.LAT - .02)):
            memory.record(latitude, self.LON, (self.DAY - dt.timedelta(days=offset)).isoformat(), 10)
        self.assertFalse(self.known(memory))

    def test_duplicate_date_spellings_count_as_one_day(self):
        memory = self.memory(days=(1,))
        cell = memory.cells[Memory.key(self.LAT, self.LON)]
        cell['20261113'] = 10
        cell['2026-W46-5'] = 10
        self.assertFalse(self.known(memory))

    def test_unknown_fire_power_still_records_observed_heat(self):
        self.assertTrue(self.known(self.memory(power=None)))

    def test_invalid_history_cannot_manufacture_persistence(self):
        memory = self.memory(days=(1, 2))
        cell = memory.cells[Memory.key(self.LAT, self.LON)]
        for date, power in (('2026-11-11', float('nan')), ('2026-11-10', -1),
                            ('2026-11-09', True), ('not-a-date', 10), ('2026-11-08', None)):
            cell[date] = power
        self.assertFalse(self.known(memory))
        self.assertFalse(self.known(SimpleNamespace(cells={'bad': {'2026-11-10': 10}})))
        self.assertFalse(self.known(None))

    def test_negative_memory_keys_use_floor_cell_centres(self):
        memory = self.memory(lat=-25.019, lon=-57.019)
        self.assertEqual(Memory.key(-25.019, -57.019), '-1251,-2851')
        self.assertTrue(persistent_local_heat(memory, -25.019, -57.019, self.DAY,
                                              {'known_heat_exclusion_km': 1.6}))

    def test_dateline_cells_are_neighbours_in_physical_space(self):
        memory = self.memory(lat=25.01, lon=-179.99)
        self.assertTrue(persistent_local_heat(memory, 25.01, 179.99, self.DAY, {}))

    def test_configuration_and_malformed_positions_are_bounded(self):
        memory = self.memory()
        self.assertFalse(self.known(memory, known_heat_km=0))
        self.assertFalse(self.known(memory, known_heat_exclusion_km=0))
        self.assertFalse(self.known(memory, known_heat_min_days=4))
        self.assertTrue(self.known(memory, known_heat_min_days=float('nan')))
        for lat, lon in ((float('nan'), self.LON), (91, 57), (25, float('inf')), (True, 57)):
            self.assertFalse(persistent_local_heat(memory, lat, lon, self.DAY, {}))

    def test_date_filtering_does_not_mutate_the_memory(self):
        memory = self.memory()
        before = copy.deepcopy(memory.cells)
        self.assertTrue(self.known(memory))
        self.assertEqual(memory.cells, before)

    def test_rule_signature_is_json_safe_and_matches_effective_defaults(self):
        default = history_rules({})
        self.assertEqual(default, history_rules({'known_heat_km': 10, 'known_heat_min_days': 3.9,
                                                 'known_heat_exclusion_km': 3.0}))
        invalid = history_rules({'known_heat_km': float('nan'), 'known_heat_min_days': float('inf'),
                                 'known_heat_exclusion_km': True})
        self.assertEqual(invalid, default)
        self.assertEqual(json.loads(json.dumps(invalid, allow_nan=False)), default)


if __name__ == '__main__':
    unittest.main()

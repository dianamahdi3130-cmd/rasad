"""A cloud cluster must not take every review slot from another part of the sea."""
import random
import unittest
from unittest.mock import patch

from rasad import daywatch


class CandidateBudgetTests(unittest.TestCase):
    def setUp(self):
        self.cluster = [(250 - i, 10 + i // 10, 10 + i % 10, 1) for i in range(45)]
        self.other = (60, 800, 800, 1)
        self.ranked = sorted(self.cluster + [self.other], reverse=True)

    def test_crowded_cell_does_not_hide_a_weaker_independent_cell(self):
        selected = daywatch.select_for_validation(self.ranked, 4, (1000, 1000))
        self.assertEqual(len(selected), 4)
        self.assertEqual(selected[0], self.ranked[0])
        self.assertIn(self.other, selected)
        self.assertEqual(len(set(selected)), len(selected))

    def test_zero_one_and_full_budgets_retain_their_contract(self):
        self.assertEqual(daywatch.select_for_validation(self.ranked, 0, (1000, 1000)), [])
        self.assertEqual(daywatch.select_for_validation(self.ranked, 1, (1000, 1000)), self.ranked[:1])
        unsorted = list(reversed(self.ranked))
        self.assertEqual(daywatch.select_for_validation(unsorted, len(unsorted), (1000, 1000)), unsorted)

    def test_overflow_selection_is_independent_of_input_enumeration(self):
        expected = daywatch.select_for_validation(self.ranked, 8, (1000, 1000))
        for seed in range(5):
            shuffled = self.ranked[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(daywatch.select_for_validation(shuffled, 8, (1000, 1000)), expected)

    def test_one_cell_still_selects_strongest_candidates(self):
        selected = daywatch.select_for_validation(self.cluster, 5, (1000, 1000))
        self.assertEqual(selected, sorted(self.cluster, reverse=True)[:5])

    def test_actual_finder_checks_a_valid_spot_outside_stronger_reflections(self):
        # Reuse the existing image/channel/solar fixture, not the selector output.
        from test_daywatch import DaywatchTests
        fixture = DaywatchTests("test_new_red_spot_is_only_an_experimental_candidate")
        fixture.setUp()
        fixture.cfg["seawatch"]["daytime"]["max_candidates"] = 4
        for row, col in ((3, 3), (3, 5), (5, 3), (5, 5)):
            fixture.data[("fire-rgb", 0, fixture.WHEN)][row, col] = 220
            fixture.data[("fire-rgb", 2, fixture.WHEN)][row, col] = 50
        spots, notes = fixture.find()
        self.assertEqual(len(spots), 1)
        self.assertEqual(notes["validated"], 4)
        self.assertEqual(notes["candidates"], 5)
        self.assertEqual(notes["rejected"]["validation budget"], 1)
        with patch.object(daywatch, "select_for_validation", side_effect=lambda rows, limit, *_: rows[:limit]):
            previous, _ = fixture.find()
        self.assertEqual(previous, [], "The same four global strongest reflections crowded out the valid spot")


if __name__ == "__main__":
    unittest.main()

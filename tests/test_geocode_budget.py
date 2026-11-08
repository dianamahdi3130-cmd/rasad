"""Optional place naming must not prevent a completed satellite scan from publishing."""
import unittest
from unittest.mock import Mock, patch

import requests

from rasad.geodb import Geo


class GeocodeBudgetTests(unittest.TestCase):
    def geo(self, state=None, **settings):
        obj = Geo.__new__(Geo)
        obj.cfg = {"_background_media": True, "geodata": {
            "nominatim_url": "https://example.invalid/reverse", **settings}}
        obj.state = state if state is not None else {}
        obj._last_nominatim = 0
        obj.s = Mock()
        obj.s.get.return_value = Mock(status_code=200)
        obj.s.get.return_value.json.return_value = {"address": {"country": "عُمان", "town": "خصب"}}
        return obj

    def test_one_uncached_name_uses_short_timeout_then_later_names_remain_retryable(self):
        geo = self.geo()
        with patch("rasad.geodb.time.monotonic", return_value=100), \
                patch("rasad.geodb.time.time", return_value=1000):
            self.assertEqual(geo.reverse(26.0, 56.0)["country"], "عُمان")
            self.assertEqual(geo.reverse(25.0, 55.0), {})
        geo.s.get.assert_called_once()
        self.assertEqual(geo.s.get.call_args.kwargs["timeout"], 8)
        self.assertTrue(geo.naming_deferred)
        self.assertNotIn("25.0,55.0", geo.state["geocode"])

    def test_cached_names_remain_available_even_with_zero_network_budget(self):
        state = {"geocode": {"26.0,56.0": {"country": "عُمان"}}}
        geo = self.geo(state, reverse_budget_s=0)
        self.assertEqual(geo.reverse(26.0, 56.0), {"country": "عُمان"})
        geo.s.get.assert_not_called()
        self.assertFalse(getattr(geo, "naming_deferred", False))

    def test_failed_lookup_does_not_poison_cache_and_retries_on_a_later_cycle(self):
        geo = self.geo()
        geo.s.get.side_effect = requests.Timeout("source timeout")
        with patch("rasad.geodb.time.monotonic", return_value=100), \
                patch("rasad.geodb.time.time", return_value=1000), self.assertLogs("rasad", "WARNING"):
            self.assertEqual(geo.reverse(26.0, 56.0), {})
        self.assertNotIn("26.0,56.0", geo.state["geocode"])
        self.assertTrue(geo.naming_deferred)
        later = self.geo(geo.state)
        with patch("rasad.geodb.time.monotonic", return_value=200), \
                patch("rasad.geodb.time.time", return_value=1100):
            self.assertEqual(later.reverse(26.0, 56.0)["country"], "عُمان")
        later.s.get.assert_called_once()

    def test_legacy_empty_cached_results_are_not_treated_as_permanent_answers(self):
        geo = self.geo({"geocode": {"26.0,56.0": {}}})
        with patch("rasad.geodb.time.monotonic", return_value=100), \
                patch("rasad.geodb.time.time", return_value=1000):
            self.assertEqual(geo.reverse(26.0, 56.0)["country"], "عُمان")
        geo.s.get.assert_called_once()

    def test_rate_limit_wait_cannot_consume_more_than_remaining_budget(self):
        geo = self.geo(reverse_budget_s=0.5)
        geo._last_nominatim = 1000
        with patch("rasad.geodb.time.monotonic", return_value=100), \
                patch("rasad.geodb.time.time", return_value=1000), \
                patch("rasad.geodb.time.sleep") as sleep:
            self.assertEqual(geo.reverse(26.0, 56.0), {})
        sleep.assert_not_called()
        geo.s.get.assert_not_called()
        self.assertTrue(geo.naming_deferred)

    def test_empty_or_error_responses_do_not_become_cached_names(self):
        for response in (Mock(status_code=429), Mock(status_code=200)):
            with self.subTest(status=response.status_code):
                response.json.return_value = {"error": "no address"}
                geo = self.geo()
                geo.s.get.return_value = response
                with patch("rasad.geodb.time.monotonic", return_value=100), \
                        patch("rasad.geodb.time.time", return_value=1000):
                    self.assertEqual(geo.reverse(26.0, 56.0), {})
                self.assertNotIn("26.0,56.0", geo.state["geocode"])
                self.assertTrue(geo.naming_deferred)


if __name__ == "__main__":
    unittest.main()

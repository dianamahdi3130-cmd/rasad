"""A plant's flare far busier than usual: noticed for the app only, never posted.

The readings below are the real Meteosat-12 strengths (MW) at Shedgum gas plant and Yanbu
industrial city on three nights, every picture with a fire symbol within 12 km
(docs/reviews/yanbu_shedgum_20261112.md). Only the night of 11-12 Nov was busier than usual."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rasad import engine, flares
from rasad.brain import Memory
from rasad.util import UTC, iso, load_json, save_json

REAL = {
    "shedgum": {
        "1109 18:00": 6.7, "1109 18:10": 6.9, "1109 18:30": 8.8, "1109 18:40": 7.0, "1109 19:20": 9.1, "1109 19:30": 10.6,
        "1109 21:40": 10.5, "1109 22:00": 8.9, "1109 22:10": 10.7, "1110 00:50": 6.7, "1110 01:10": 9.0, "1110 01:40": 7.6,
        "1110 19:30": 9.7, "1110 19:40": 15.4, "1110 20:00": 10.0, "1111 18:40": 19.6, "1111 19:00": 11.5, "1111 20:40": 8.4,
        "1111 20:50": 8.3, "1111 21:00": 18.1, "1111 21:10": 12.0, "1111 21:30": 13.3, "1111 21:40": 6.3, "1111 21:50": 12.2,
        "1111 22:00": 11.0, "1111 22:10": 10.9, "1111 22:20": 12.0, "1111 22:30": 9.5, "1111 22:40": 20.3, "1111 23:00": 10.8,
        "1111 23:10": 19.0, "1111 23:20": 23.9, "1111 23:30": 11.7, "1111 23:40": 10.2, "1111 23:50": 10.2, "1112 00:10": 7.2,
        "1112 01:00": 11.8, "1112 01:20": 20.6, "1112 01:30": 7.8, "1112 01:50": 6.3},
    "yanbu": {
        "1109 23:40": 13.0, "1110 01:00": 9.2, "1110 01:10": 13.6, "1110 01:20": 9.9, "1110 19:20": 5.1, "1110 19:30": 7.5,
        "1110 19:50": 6.2, "1110 20:40": 5.8, "1110 20:50": 6.2, "1110 21:00": 7.1, "1111 18:00": 7.2, "1111 18:10": 6.0,
        "1111 18:50": 5.1, "1111 19:10": 6.8, "1111 19:50": 5.5, "1111 20:10": 6.6, "1111 20:20": 5.6, "1111 20:30": 5.5,
        "1111 20:40": 7.4, "1111 20:50": 5.2, "1111 21:00": 6.2, "1111 21:10": 6.7, "1111 21:20": 6.7, "1111 21:50": 10.2,
        "1111 22:00": 7.4, "1111 22:10": 8.1, "1111 22:20": 5.1, "1111 22:40": 7.1, "1111 23:00": 16.8, "1111 23:10": 17.5,
        "1111 23:20": 14.5, "1111 23:40": 5.2, "1111 23:50": 6.1, "1112 00:00": 7.5, "1112 00:20": 5.8, "1112 00:30": 5.3,
        "1112 00:40": 7.9, "1112 00:50": 5.2, "1112 01:00": 8.1, "1112 01:10": 5.9, "1112 01:20": 5.1, "1112 01:30": 11.4,
        "1112 01:40": 9.1},
}
SITES = {"shedgum": (25.6477, 49.3922, 6.93, "معمل شدقم للغاز الطبيعي"),
         "yanbu": (24.02, 38.21, 3.55, "مصفاة ينبع")}


def when(key):
    return f"2026-{key[:2]}-{key[2:4]}T{key[5:]}:00Z"


def night(key):
    """Which night a reading belongs to: 18:00 UTC to 06:00 UTC."""
    day = int(key[2:4])
    return f"{day}-{day + 1}" if key[5:7] >= "18" else f"{day - 1}-{day}"


def symbol(site, key, frp):
    lat, lon, _, _ = SITES[site]
    return {"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": when(key),
            "lat": lat + 0.01, "lon": lon - 0.01, "frp": frp}


def facility(site):
    lat, lon, _, label = SITES[site]
    return {"label": label, "lat": lat, "lon": lon}


class RealNightsTests(unittest.TestCase):
    def replay(self, site, state=None):
        state = {} if state is None else state
        busy = set()
        for key, frp in sorted(REAL[site].items()):
            if flares.observe(state, symbol(site, key, frp), SITES[site][2], facility(site)):
                busy.add(night(key))
        return state, busy

    def test_only_the_night_of_11_to_12_november_is_busier_than_usual(self):
        for site in SITES:
            with self.subTest(site=site):
                _, busy = self.replay(site)
                self.assertEqual(busy, {"11-12"})

    def test_the_app_gets_one_marker_per_plant_with_its_peak(self):
        state = {}
        for site in SITES:
            self.replay(site, state)
        now = dt.datetime(2026, 11, 12, 2, 20, tzinfo=UTC)
        shown = {e["label"]: e for e in flares.items(state, now)}
        self.assertEqual(set(shown), {"معمل شدقم للغاز الطبيعي", "مصفاة ينبع"})
        self.assertEqual(shown["معمل شدقم للغاز الطبيعي"]["peak"], 23.9)
        self.assertEqual(shown["مصفاة ينبع"]["peak"], 17.5)
        for e in shown.values():
            self.assertTrue(e["first"].startswith(("2026-11-11T2", "2026-11-12T0")))
            self.assertGreaterEqual(e["pictures"], 8)
            self.assertGreaterEqual(e["strong"], 3)
            self.assertTrue(e["id"].startswith("flare-"))

    def test_the_words_say_what_was_seen_and_never_a_cause(self):
        ep = {"pictures": 14, "strong": 4, "peak": 17.5, "typical": 3.55, "window_h": 3}
        self.assertEqual(flares.text(ep, "مصفاة ينبع"),
                         "اشتعلت الشعلة في مصفاة ينبع أكثر من المعتاد: رمز حريق في 14 صورة خلال 3 ساعات، "
                         "منها 4 بقوة ضعف المعتاد أو أكثر. علامة تجريبية لا تُرسل إلى القناة؛ "
                         "قد تكون حرقاً تشغيلياً للغاز أو أثر ضرر، والصور وحدها لا تفرّق بينهما.")
        self.assertIn("\u2068Shedgum\u2069", flares.text(ep, "معمل غاز (Shedgum)"))


class RuleTests(unittest.TestCase):
    T0 = dt.datetime(2026, 11, 11, 21, 0, tzinfo=UTC)

    def feed(self, state, values, start=T0, typical=4.0):
        hit = False
        for k, frp in enumerate(values):
            t = (start + dt.timedelta(minutes=10 * k)).strftime("%m%d %H:%M")
            hit = flares.observe(state, symbol("yanbu", t, frp), typical, facility("yanbu")) or hit
        return hit

    def test_many_pictures_without_strong_readings_are_the_usual_flare(self):
        self.assertFalse(self.feed({}, [6.0] * 18))

    def test_a_few_strong_readings_alone_are_not_enough(self):
        self.assertFalse(self.feed({}, [20.0, 22.0, 25.0, 21.0, 6.0]))

    def test_a_reading_whose_strength_comes_later_counts_when_it_arrives(self):
        state = {}
        self.assertFalse(self.feed(state, [6.0] * 6 + [None, None, 15.0, 16.0]))
        late = symbol("yanbu", (self.T0 + dt.timedelta(minutes=60)).strftime("%m%d %H:%M"), 14.0)
        self.assertTrue(flares.observe(state, late, 4.0, facility("yanbu")))

    def test_needs_a_plant_and_its_usual_heat(self):
        self.assertFalse(flares.observe({}, symbol("yanbu", "1111 21:00", 30.0), None, facility("yanbu")))
        self.assertFalse(flares.observe({}, symbol("yanbu", "1111 21:00", 30.0), 4.0, None))
        other = dict(symbol("yanbu", "1111 21:00", 30.0), src="mtg_spot")
        self.assertFalse(flares.observe({}, other, 4.0, facility("yanbu")))

    def test_old_pictures_and_old_markers_are_forgotten(self):
        state = {}
        self.assertTrue(self.feed(state, [6.0] * 6 + [15.0, 16.0, 17.0]))
        flares.tidy(state, self.T0 + dt.timedelta(hours=6))
        self.assertEqual(state["flare_watch"][next(iter(state["flare_watch"]))]["seen"], {})
        self.assertEqual(len(flares.items(state, self.T0 + dt.timedelta(hours=6))), 1)
        flares.tidy(state, self.T0 + dt.timedelta(hours=60))
        self.assertEqual(state["flare_watch"], {})


class WholeCheckTests(unittest.TestCase):
    """A busy flare in a real check: shown in the app's data, no incident, nothing posted."""

    def test_busy_flare_is_shown_in_the_app_and_never_posted(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        now = dt.datetime(2026, 11, 11, 23, 30, tzinfo=UTC)
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
        cfg["regions"] = [{"id": "sa", "name": "السعودية", "bbox": [34, 16, 56, 32]}]
        cfg["alerts"]["min_verdict"] = "يحتاج تأكيداً"
        cfg["seawatch"].update(enabled=True, look_back=False)
        cfg["eumetview"]["fire_layers"] = []
        cfg["firms"]["map_key"] = "offline-test-key"
        cfg["clip"]["enabled"] = False
        cfg["telegram"].update(bot_token="offline-test-token", chat_id="offline-test-chat")
        save_json(cfg["_state_dir"] / "state.json", {
            "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
            "sea_check": engine.SEA_CHECK, "hello_sent": iso(now), "bootstrapped": iso(now)})
        lat, lon, typical, label = SITES["yanbu"]
        memory = Memory({})
        for k in range(1, 21):   # a steady flare: seen on 20 of the last 30 days at about 3.5 MW
            memory.record(lat, lon, (now - dt.timedelta(days=k)).date().isoformat(), 3.5)
        save_json(cfg["_state_dir"] / "memory.json", memory.cells)
        tonight = [(key, frp) for key, frp in sorted(REAL["yanbu"].items())
                   if "1111 20:30" <= key <= "1111 23:20"]
        dets = [symbol("yanbu", key, frp) for key, frp in tonight]
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "land"
        geo.facility_within.return_value = True
        geo.facility.return_value = facility("yanbu")
        geo.describe.return_value = ("في ينبع", "السعودية")
        deliver = Mock(return_value=789)
        forbidden = Mock(side_effect=AssertionError("no network or Telegram in this test"))
        session = Mock()
        for method in ("get", "post", "put"):
            getattr(session, method).side_effect = AssertionError("No network in these tests")
        with patch.multiple(engine, utcnow=Mock(return_value=now), make_session=Mock(return_value=session),
                            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(),
                            latest_time=forbidden, detect_geo_layer=forbidden,
                            firms_fetch=Mock(return_value=[]), add_clip=Mock(return_value=False),
                            deliver=deliver, tg_send=forbidden), \
                patch.object(engine.seawatch, "watch", return_value=copy.deepcopy(dets)):
            state = engine.run_cycle(cfg, send=True)
        deliver.assert_not_called()
        self.assertEqual(state.incidents, [])
        meta = load_json(cfg["_site_dir"] / "data" / "meta.json", {})
        self.assertEqual([(e["label"], e["peak"]) for e in meta["flare_activity"]], [(label, 17.5)])
        self.assertEqual(load_json(cfg["_site_dir"] / "data" / "incidents.json", {})["items"], [])
        # the busy flare does not change what the place's usual heat is learned to be
        self.assertEqual(Memory(state.memory).profile(lat, lon, "2026-11-12", 30, 1)[1], 3.5)


if __name__ == "__main__":
    unittest.main()

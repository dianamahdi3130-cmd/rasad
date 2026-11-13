"""A first night spot reaches the channel without becoming a confirmed fire."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rasad import earlyalerts, engine
from rasad.util import UTC, iso


class EarlyNightAlertTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 13, 19, 0, tzinfo=UTC)
    WHEN = NOW - dt.timedelta(minutes=10)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_site_dir=Path(self.directory.name) / "site",
                        _state_dir=Path(self.directory.name) / "state",
                        app_url="https://example.invalid/rasad")
        self.cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 22.4, 60.2, 27.2]}]
        self.cfg["seawatch"]["early_alerts"] = dict(earlyalerts.DEFAULTS, enabled=True)
        self.S = SimpleNamespace(state={"sea_candidates": [self.candidate()]}, incidents=[],
                                 memory={"preserved": True}, sent=False)

    def candidate(self, **changes):
        row = {"src": "mtg_spot", "t": iso(self.WHEN), "lat": 26.3716, "lon": 56.2988,
               "rise": 53.3, "reference_minutes": [30],
               "source_grid": {"box_id": "hormuz_oman", "bbox": [55, 22.4, 60.2, 27.2],
                               "width": 1051, "height": 1069, "km_per_px": .5,
                               "layer": "mtg_fd:rgb_firetemperature"}}
        row.update(changes)
        return row

    def record(self):
        return next(iter(self.S.state["early_night"].values()))

    def sync(self, now=None):
        return earlyalerts.sync(self.cfg, self.S, now or self.NOW)

    def asset(self, snapshot):
        root = self.cfg["_site_dir"] / "clips"
        root.mkdir(parents=True, exist_ok=True)
        for name in ("first.mp4", "first.png"):
            (root / name).write_bytes(b"offline-media")
        snapshot.update(clip="clips/first.mp4", snap="clips/first.png")
        return True

    def incident(self, **changes):
        result = {"id": "real-incident", "observations": [dict(self.candidate())],
                  "alerted_verdict": None, "max_frp": None, "status": "new"}
        result.update(changes)
        return result

    def test_first_slot_posts_immediately_without_incident_or_memory_writes(self):
        self.assertEqual(self.sync(), 1)
        saved_memory = copy.deepcopy(self.S.memory)
        post, prepare = Mock(return_value=123), Mock(return_value=False)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, prepare), 1)
        message, media = post.call_args.args
        self.assertIn("رصد حراري أولي", message)
        self.assertIn("يحتاج تأكيداً", message)
        self.assertIn("21:50", message)
        self.assertIn("MTG", message)
        self.assertNotIn("53.3", message)
        self.assertNotIn(" MW", message)
        self.assertIsNone(media)
        self.assertEqual(self.S.incidents, [])
        self.assertEqual(self.S.memory, saved_memory)
        self.assertTrue(self.S.sent)
        self.assertEqual(self.record()["tg_msg"], 123)
        self.assertTrue(self.record()["video_pending"])
        self.assertNotIn("rise", json.dumps(self.record()))
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, prepare), 0)
        post.assert_called_once()

    def test_ready_video_attaches_to_first_post_and_never_repeats(self):
        self.sync()
        post = Mock(return_value=101)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, self.asset), 1)
        self.assertEqual(post.call_args.args[1], self.cfg["_site_dir"] / "clips/first.mp4")
        self.assertFalse(self.record()["video_pending"])
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, self.asset), 0)
        post.assert_called_once()

    def test_video_followup_replies_once_and_survives_state_reload(self):
        self.sync()
        post = Mock(return_value=101)
        earlyalerts.deliver(self.cfg, self.S, self.NOW, post, Mock(return_value=False))
        self.S.state = json.loads(json.dumps(self.S.state))
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, self.asset), 1)
        self.assertIn("فيديو الرصد الحراري الأولي", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["reply_to"], 101)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, self.asset), 0)
        self.assertEqual(post.call_count, 2)

    def test_failed_first_send_remains_unsent_and_retryable(self):
        self.sync()
        post = Mock(side_effect=[None, 72])
        prepare = Mock(return_value=False)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, prepare), 0)
        self.assertNotIn("sent_at", self.record())
        self.assertFalse(self.S.sent)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, prepare), 1)
        self.assertEqual(self.record()["tg_msg"], 72)

    def test_failed_video_send_retries_without_repeating_first_notice(self):
        self.sync()
        post = Mock(side_effect=[80, None, 81])
        earlyalerts.deliver(self.cfg, self.S, self.NOW, post, Mock(return_value=False))
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, self.asset), 0)
        self.assertTrue(self.record()["video_pending"])
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, self.asset), 1)
        self.assertFalse(self.record()["video_pending"])
        self.assertEqual(post.call_args.kwargs["reply_to"], 80)

    def test_preparation_queues_once_and_preserves_original_grid_and_reference(self):
        self.sync()
        def enqueue(cfg, snapshot, now):
            snapshot["media_job"] = "a" * 64
            return False
        request = Mock(side_effect=enqueue)
        self.assertEqual(earlyalerts.prepare(self.cfg, self.S, self.NOW, request), 1)
        snapshot = copy.deepcopy(self.record()["media_snapshot"])
        self.S.state["sea_candidates"].append(self.candidate(t=iso(self.WHEN + dt.timedelta(minutes=10))))
        self.sync()
        with patch.object(earlyalerts.mediaqueue, "import_result", return_value=("waiting", {})):
            self.assertEqual(earlyalerts.prepare(self.cfg, self.S, self.NOW, request), 0)
        request.assert_called_once()
        self.assertEqual(self.record()["media_snapshot"], snapshot)
        self.assertEqual(snapshot["source_grid"], self.candidate()["source_grid"])
        self.assertEqual(snapshot["reference_minutes"], [30])
        self.assertEqual(snapshot["last"], iso(self.WHEN))
        self.assertEqual(self.record()["last"], iso(self.NOW))

    def test_worker_result_is_imported_and_failed_jobs_not_requeued(self):
        self.sync()
        record = self.record()
        snapshot = earlyalerts._snapshot(record)
        snapshot["media_job"] = "b" * 64
        request = Mock()
        with patch.object(earlyalerts.mediaqueue, "import_result", return_value=("failed", {"clip_note": "retry"})):
            earlyalerts.prepare(self.cfg, self.S, self.NOW, request)
        request.assert_not_called()
        self.assertEqual(record["clip_note"], "retry")
        self.asset(snapshot)
        fields = {"clip": snapshot["clip"], "snap": snapshot["snap"], "clip_updated_at": iso(self.NOW)}
        with patch.object(earlyalerts.mediaqueue, "import_result", return_value=("done", fields)):
            earlyalerts.prepare(self.cfg, self.S, self.NOW, request)
        self.assertEqual(record["clip"], "clips/first.mp4")
        self.assertNotIn("media_job", snapshot)
        self.assertNotIn("clip_note", record)

    def test_matching_real_origin_promotes_thread_and_never_trains_memory(self):
        self.sync()
        earlyalerts.deliver(self.cfg, self.S, self.NOW, Mock(return_value=707), Mock(return_value=False))
        real = self.incident()
        self.S.incidents.append(real)
        self.sync()
        self.assertEqual(real["tg_msg"], 707)
        self.assertEqual(real["status"], "alerted")
        self.assertEqual(real["alerted_verdict"], "يحتاج تأكيداً")
        self.assertIsNone(real["alerted_max_frp"])
        self.assertEqual(real["early_ids"], [self.record()["id"]])
        self.assertEqual(self.record()["promoted_to"], real["id"])
        self.assertEqual(earlyalerts.items(self.cfg, self.S.state, self.S.incidents, self.NOW), [])
        self.assertEqual(self.S.memory, {"preserved": True})
        self.assertEqual(len(self.S.incidents), 1)

    def test_already_alerted_real_record_is_not_overwritten(self):
        self.sync()
        earlyalerts.deliver(self.cfg, self.S, self.NOW, Mock(return_value=10), Mock(return_value=False))
        real = self.incident(alerted_verdict="مؤكد", tg_msg=999, alerted_at=iso(self.WHEN),
                             alerted_max_frp=47, status="alerted")
        self.S.incidents.append(real)
        self.sync()
        self.assertEqual(real["tg_msg"], 999)
        self.assertEqual(real["alerted_verdict"], "مؤكد")
        self.assertEqual(real["alerted_max_frp"], 47)

    def test_multi_origin_promotion_is_explicitly_ambiguous_and_does_not_choose_a_thread(self):
        self.S.state["sea_candidates"].append(self.candidate(lat=26.38))
        self.sync()
        earlyalerts.deliver(self.cfg, self.S, self.NOW, Mock(side_effect=[10, 11]), Mock(return_value=False))
        real = self.incident(observations=copy.deepcopy(self.S.state["sea_candidates"]))
        self.S.incidents.append(real)
        self.sync()
        self.assertTrue(real["early_promotion_ambiguous"])
        self.assertEqual(len(real["early_ids"]), 2)
        self.assertNotIn("tg_msg", real)
        self.assertIsNone(real["alerted_verdict"])
        self.assertTrue(all(row.get("promotion_ambiguous") for row in self.S.state["early_night"].values()))
        self.assertEqual(earlyalerts.items(self.cfg, self.S.state, [real], self.NOW), [])

    def test_ready_initial_clip_is_preserved_on_promoted_incident_and_flagged_when_sent(self):
        self.sync()
        earlyalerts.deliver(self.cfg, self.S, self.NOW, Mock(return_value=44), Mock(return_value=False))
        real = self.incident()
        self.S.incidents.append(real)
        self.sync()
        post = Mock(return_value=45)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, self.asset), 1)
        self.assertEqual(real["clip"], "clips/first.mp4")
        self.assertEqual(real["snap"], "clips/first.png")
        self.assertTrue(real["early_clip"])
        self.assertEqual(real["early_clip_time"], iso(self.WHEN))
        self.assertEqual(real["early_video_sent_at"], iso(self.NOW))
        self.assertEqual(post.call_args.kwargs["reply_to"], 44)
        self.assertNotIn("لم يؤكدها", post.call_args.args[0])

    def test_promotion_requires_own_acquisition_not_a_nearby_different_event(self):
        self.sync()
        for change in ({"src": "mtg_frp"}, {"t": iso(self.NOW)}, {"lat": 26.38}):
            with self.subTest(change=change):
                real = self.incident(observations=[self.candidate(**change)])
                self.S.incidents = [real]
                self.sync()
                self.assertNotIn("promoted_to", self.record())
                self.assertIsNone(real["alerted_verdict"])

    def test_already_established_origin_does_not_create_an_early_duplicate(self):
        self.S.incidents.append(self.incident())
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.S.state["early_night"], {})

    def test_same_image_nearby_spots_stay_separate_and_ambiguity_stays_unmerged(self):
        self.S.state["sea_candidates"].append(self.candidate(lat=26.38))
        self.assertEqual(self.sync(), 2)
        self.S.state["sea_candidates"].append(self.candidate(t=iso(self.NOW), lat=26.375))
        self.assertEqual(self.sync(), 1)
        self.assertEqual(len(self.S.state["early_night"]), 3)

    def test_subsequent_acquisition_matches_anchor_without_distance_chaining(self):
        self.sync()
        self.S.state["sea_candidates"].append(self.candidate(t=iso(self.NOW), lat=26.38))
        self.assertEqual(self.sync(), 0)
        self.assertEqual(len(self.S.state["early_night"]), 1)
        self.S.state["sea_candidates"].append(self.candidate(t=iso(self.NOW + dt.timedelta(minutes=10)), lat=26.391))
        self.assertEqual(self.sync(self.NOW + dt.timedelta(minutes=10)), 1)
        self.assertEqual(len(self.S.state["early_night"]), 2)

    def test_new_episode_after_gap_is_not_relabelled_an_update(self):
        self.sync()
        later = self.WHEN + dt.timedelta(minutes=50)
        self.S.state["sea_candidates"] = [self.candidate(t=iso(later))]
        self.assertEqual(self.sync(later + dt.timedelta(minutes=5)), 1)
        self.assertEqual(len(self.S.state["early_night"]), 2)

    def test_old_install_day_source_flares_and_disabled_modes_cannot_post(self):
        cases = [self.candidate(t=iso(self.NOW - dt.timedelta(minutes=46))),
                 self.candidate(src="mtg_day_spot"), self.candidate(t="2026-11-13T09:00:00Z")]
        self.S.state["sea_candidates"] = cases
        self.assertEqual(self.sync(), 0)
        self.S.state["sea_candidates"] = [self.candidate()]
        key = f"{int(26.3716 // .05)},{int(56.2988 // .05)}"
        self.S.state["sea_sparkle"] = {key: ["2026-11-10", "2026-11-11", "2026-11-12"]}
        self.assertEqual(self.sync(), 0)
        for setting in ("enabled", "alerts"):
            self.cfg["seawatch"][setting] = False
            self.assertFalse(earlyalerts.enabled(self.cfg))
            self.cfg["seawatch"][setting] = True
        self.cfg["seawatch"]["early_alerts"]["enabled"] = False
        self.assertEqual(self.sync(), 0)
        self.assertEqual(earlyalerts.items(self.cfg, self.S.state, [], self.NOW), [])

    def test_first_post_cap_leaves_remaining_records_retryable(self):
        self.S.state["sea_candidates"] = [self.candidate(lat=26.35 + .01 * i) for i in range(5)]
        self.sync()
        post = Mock(return_value=100)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, Mock(return_value=False)), 3)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, Mock(return_value=False)), 2)
        self.assertEqual(sum(bool(row.get("sent_at")) for row in self.S.state["early_night"].values()), 5)

    def test_clip_disabled_sends_only_text_without_any_media_requests(self):
        self.cfg["clip"]["enabled"] = False
        self.sync()
        request, post = Mock(side_effect=AssertionError("No clip request")), Mock(return_value=30)
        self.assertEqual(earlyalerts.prepare(self.cfg, self.S, self.NOW, request), 0)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, self.NOW, post, request), 1)
        self.assertFalse(self.record()["video_pending"])
        request.assert_not_called()

    def test_expiry_is_quiet_and_persistent_items_have_no_internal_indices(self):
        self.sync()
        post = Mock(return_value=4)
        earlyalerts.deliver(self.cfg, self.S, self.NOW, post, Mock(return_value=False))
        later = self.NOW + dt.timedelta(hours=5)
        self.assertEqual(earlyalerts.deliver(self.cfg, self.S, later, post, self.asset), 0)
        view = earlyalerts.items(self.cfg, self.S.state, [], later)
        self.assertEqual(len(view), 1)
        self.assertIn("لم يتأكد", view[0]["headline"])
        self.assertNotIn("source_grid", view[0])
        self.assertNotIn("observations", view[0])
        self.assertIsNone(view[0]["max_frp"])
        self.assertIsNone(view[0]["frp_series"])
        post.assert_called_once()
        self.sync(self.NOW + dt.timedelta(days=8))
        self.assertEqual(self.S.state["early_night"], {})

    def test_public_views_do_not_mutate_stored_records(self):
        self.sync()
        before = copy.deepcopy(self.S.state)
        view = earlyalerts.items(self.cfg, self.S.state, [], self.NOW)
        view[0]["regions"].append("changed")
        self.assertEqual(self.S.state, before)

    def test_disabled_early_mode_preserves_quiet_app_previews(self):
        self.cfg["seawatch"]["early_alerts"]["enabled"] = False
        self.assertEqual(self.sync(), 0)
        result = earlyalerts.items(self.cfg, self.S.state, [], self.NOW)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["preliminary"])
        self.assertNotIn("early_night", self.S.state)

    def test_publish_without_sync_and_older_quiet_candidates_remain_visible_without_duplicates(self):
        self.assertEqual(len(earlyalerts.items(self.cfg, self.S.state, [], self.NOW)), 1)
        self.sync()
        self.assertEqual(len(earlyalerts.items(self.cfg, self.S.state, [], self.NOW)), 1)
        self.S.state["sea_candidates"].append(self.candidate(t=iso(self.NOW - dt.timedelta(minutes=46)), lat=26.4))
        self.assertEqual(self.sync(), 0)
        self.assertEqual(len(earlyalerts.items(self.cfg, self.S.state, [], self.NOW)), 2)


if __name__ == "__main__":
    unittest.main()

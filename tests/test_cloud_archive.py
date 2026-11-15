"""The long-term record goes up to the private app one month at a time, and only when it changed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

from rasad import cloud


class SendArchive(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.site = Path(self.folder.name) / "site"
        self.archive = self.site / "data" / "archive"
        self.archive.mkdir(parents=True)
        (self.archive / "2026-10.json").write_text(json.dumps({"month": "2026-10", "items": [1]}), encoding="utf-8")
        (self.archive / "2026-11.json").write_text(json.dumps({"month": "2026-11", "items": [1, 2]}), encoding="utf-8")
        (self.archive / "index.json").write_text("{}", encoding="utf-8")
        self.session = Mock()
        self.session.put.return_value = Mock(ok=True, status_code=200)

    def sent(self):
        return [call.args[0].rsplit("/", 1)[1] for call in self.session.put.call_args_list]

    def test_each_month_goes_up_once_and_again_only_when_it_changes(self):
        self.assertEqual(cloud.send_archive(self.site, self.session, "https://app.test"), 2)
        self.assertEqual(self.sent(), ["2026-10", "2026-11"])           # the list of months itself is not sent
        self.assertTrue(self.session.put.call_args_list[0].args[0].startswith("https://app.test/api/ingest/archive/"))
        self.session.put.reset_mock()
        self.assertEqual(cloud.send_archive(self.site, self.session, "https://app.test"), 0)
        self.session.put.assert_not_called()
        (self.archive / "2026-11.json").write_text(json.dumps({"month": "2026-11", "items": [1, 2, 3]}), encoding="utf-8")
        self.assertEqual(cloud.send_archive(self.site, self.session, "https://app.test"), 1)
        self.assertEqual(self.sent(), ["2026-11"])

    def test_a_refused_or_failed_month_is_tried_again_next_time(self):
        self.session.put.side_effect = [Mock(ok=False, status_code=500), requests.ConnectionError("down")]
        self.assertEqual(cloud.send_archive(self.site, self.session, "https://app.test"), 0)
        self.session.put.side_effect = None
        self.session.put.reset_mock()
        self.assertEqual(cloud.send_archive(self.site, self.session, "https://app.test"), 2)

    def test_no_archive_yet_is_nothing_to_send(self):
        self.assertEqual(cloud.send_archive(Path(self.folder.name) / "empty", self.session, "https://app.test"), 0)
        self.session.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()

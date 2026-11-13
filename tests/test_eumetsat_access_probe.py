"""Access checks must prove a bounded raw read without exposing authentication data."""
from contextlib import nullcontext
import datetime as dt
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from tools import eumetsat_access_probe as probe


class ReadTracker(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class Response:
    def __init__(self, status=200, *, payload=None, body=None, headers=None):
        self.status_code = status
        self.body = body if body is not None else json.dumps(payload).encode()
        self.raw = ReadTracker(self.body)
        self.headers = headers or {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        self.raw.close()

    def iter_content(self, chunk_size):
        for pos in range(0, len(self.body), chunk_size):
            yield self.body[pos:pos + chunk_size]


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected additional HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class EumetsatAccessProbeTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 13, 15, tzinfo=dt.timezone.utc)
    KEY = "fake-consumer-key-for-offline-tests"
    SECRET = "fake-consumer-secret-for-offline-tests"
    TOKEN = "fake-access-token-for-offline-tests"
    CREDS = {"EUMETSAT_CONSUMER_KEY": KEY, "EUMETSAT_CONSUMER_SECRET": SECRET}
    HDF = b"\x89HDF\r\n\x1a\n" + b"x" * 1000

    @classmethod
    def catalogue(cls, *, entries=None, parent=None):
        return {"features": [{"properties": {
            "parentIdentifier": parent or probe.COLLECTION,
            "identifier": "product-with+punctuation,and:time",
            "links": {"sip-entries": entries if entries is not None else [{
                "title": "W_XX-EUMETSAT-CHK-BODY-example.nc",
                "href": "https://malicious.example/collect-credentials",
            }]},
        }}]}

    @classmethod
    def auth(cls):
        return Response(payload={"access_token": cls.TOKEN})

    def run_probe(self, responses, *, creds=None):
        session = Session(responses)
        result = probe.check_access(self.CREDS if creds is None else creds,
                                    session=session, now=self.NOW)
        self.assertFalse(result["fire_detector_changed"])
        for value in (self.KEY, self.SECRET, self.TOKEN):
            self.assertNotIn(value, json.dumps(result))
        return result, session

    def test_missing_or_partial_credentials_make_no_http_requests(self):
        for creds in ({}, {"EUMETSAT_CONSUMER_KEY": self.KEY},
                      {"EUMETSAT_CONSUMER_KEY": " ", "EUMETSAT_CONSUMER_SECRET": self.SECRET}):
            with self.subTest(creds=list(creds)):
                result, session = self.run_probe([], creds=creds)
                self.assertEqual(result["status"], "missing_credentials")
                self.assertFalse(result["authenticated"])
                self.assertEqual(session.calls, [])

    def test_public_catalogue_cannot_bypass_failed_authentication(self):
        rejected = Response(401, payload={"detail": self.SECRET})
        result, session = self.run_probe([rejected, Response(payload=self.catalogue())])
        self.assertEqual(result["status"], "authentication_failed")
        self.assertEqual(result["http_status"], 401)
        self.assertFalse(result["catalogue_ok"])
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(rejected.closed)

    def test_catalogue_failure_preserves_successful_authentication(self):
        result, session = self.run_probe([self.auth(), Response(400, body=self.SECRET.encode())])
        self.assertEqual(result["status"], "catalogue_failed")
        self.assertEqual(result["phase"], "catalogue")
        self.assertTrue(result["authenticated"])
        self.assertFalse(result["raw_sample_ok"])
        self.assertEqual(len(session.calls), 2)

    def test_empty_catalogue_is_not_an_authentication_or_download_failure(self):
        result, session = self.run_probe([self.auth(), Response(payload={"features": []})])
        self.assertEqual(result["status"], "no_products")
        self.assertTrue(result["authenticated"])
        self.assertTrue(result["catalogue_ok"])
        self.assertFalse(result["raw_sample_ok"])
        self.assertEqual(len(session.calls), 2)

    def test_metadata_without_body_netcdf_cannot_prove_raw_access(self):
        entries = [{"title": "W_XX-EUMETSAT-TRAILER.nc"}, {"title": "CHK-BODY.xml"}]
        result, session = self.run_probe([self.auth(), Response(payload=self.catalogue(entries=entries))])
        self.assertEqual(result["status"], "catalogue_only")
        self.assertFalse(result["raw_sample_ok"])
        self.assertEqual(len(session.calls), 2)

    def test_search_is_one_product_in_a_window_two_to_three_hours_old(self):
        result, session = self.run_probe([self.auth(), Response(payload={"features": []})])
        method, url, options = session.calls[1]
        self.assertEqual((method, url), ("GET", probe.SEARCH_URL))
        self.assertEqual(options["params"]["c"], 1)
        self.assertEqual(options["params"]["si"], 0)
        self.assertEqual(options["params"]["pi"], probe.COLLECTION)
        self.assertEqual(options["params"]["dtstart"], "2026-11-13T12:00:00Z")
        self.assertEqual(options["params"]["dtend"], "2026-11-13T13:00:00Z")
        self.assertEqual(result["search_start_utc"], options["params"]["dtstart"])
        self.assertEqual(result["search_end_utc"], options["params"]["dtend"])
        for _, _, options in session.calls:
            self.assertLessEqual(options["timeout"], 20)
            self.assertFalse(options["allow_redirects"])
            self.assertTrue(options["stream"])

    def test_canonical_download_ignores_catalogue_href_and_reads_only_64_bytes(self):
        raw = Response(206, body=self.HDF)
        result, session = self.run_probe([self.auth(), Response(payload=self.catalogue()), raw])
        self.assertEqual(result["status"], "raw_access_ok")
        self.assertTrue(result["raw_sample_ok"])
        self.assertEqual(result["bytes_read"], 64)
        method, url, options = session.calls[2]
        self.assertEqual(method, "GET")
        self.assertTrue(url.startswith("https://api.eumetsat.int/data/download/1.0.0/collections/"))
        self.assertIn("product-with%2Bpunctuation%2Cand%3Atime", url)
        self.assertNotIn("malicious.example", url)
        self.assertNotIn(self.TOKEN, url)
        self.assertEqual(options["headers"]["Range"], "bytes=0-63")
        self.assertEqual(options["headers"]["Accept-Encoding"], "identity")
        self.assertEqual(options["headers"]["Authorization"], "Bearer " + self.TOKEN)
        self.assertEqual(raw.raw.read_sizes, [64])
        self.assertTrue(raw.closed)

    def test_ignored_range_does_not_download_complete_entry(self):
        raw = Response(200, body=self.HDF + b"x" * (2 * 1024 * 1024))
        result, _ = self.run_probe([self.auth(), Response(payload=self.catalogue()), raw])
        self.assertEqual(result["status"], "raw_access_ok")
        self.assertEqual(raw.raw.read_sizes, [64])
        self.assertEqual(result["bytes_read"], 64)
        self.assertTrue(raw.closed)

    def test_html_or_xml_with_http_200_cannot_pass_as_original_data(self):
        for content in (b"<html>login " + self.TOKEN.encode(), b"<?xml version='1.0'?><error/>", b""):
            with self.subTest(content_length=len(content)):
                result, _ = self.run_probe([self.auth(), Response(payload=self.catalogue()),
                                           Response(200, body=content)])
                self.assertEqual(result["status"], "unexpected_payload")
                self.assertFalse(result["raw_sample_ok"])

    def test_supported_classic_netcdf_headers_also_prove_raw_read(self):
        for magic in (b"CDF\x01", b"CDF\x02", b"CDF\x05"):
            with self.subTest(magic=magic):
                result, _ = self.run_probe([self.auth(), Response(payload=self.catalogue()),
                                           Response(206, body=magic + b"x" * 60)])
                self.assertEqual(result["status"], "raw_access_ok")

    def test_download_permission_failure_is_distinct_from_bad_keys(self):
        for status in (401, 403):
            with self.subTest(status=status):
                rejected = Response(status, body=(self.KEY + self.SECRET + self.TOKEN).encode())
                result, _ = self.run_probe([self.auth(), Response(payload=self.catalogue()), rejected])
                self.assertEqual(result["status"], "download_denied")
                self.assertEqual(result["http_status"], status)
                self.assertTrue(result["authenticated"])
                self.assertTrue(result["catalogue_ok"])
                self.assertFalse(result["raw_sample_ok"])
                self.assertTrue(rejected.closed)

    def test_download_404_is_not_reported_as_bad_keys_or_purchase_requirement(self):
        result, _ = self.run_probe([self.auth(), Response(payload=self.catalogue()), Response(404)])
        self.assertEqual(result["status"], "download_unavailable")
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["http_status"], 404)

    def test_temporary_service_errors_are_not_classified_as_invalid_credentials(self):
        for status in (429, 500, 503):
            for phase in ("authentication", "catalogue", "download"):
                with self.subTest(status=status, phase=phase):
                    failed = Response(status, body=self.SECRET.encode(), headers={"Retry-After": "3600"})
                    responses = {"authentication": [failed],
                                 "catalogue": [self.auth(), failed],
                                 "download": [self.auth(), Response(payload=self.catalogue()), failed]}[phase]
                    result, session = self.run_probe(responses)
                    self.assertEqual(result["status"], "service_unavailable")
                    self.assertEqual(result["phase"], phase)
                    self.assertEqual(result["http_status"], status)
                    self.assertFalse(result["raw_sample_ok"])
                    self.assertEqual(len(session.calls), len(responses), "no retry/sleep storm")
                    self.assertTrue(failed.closed)

    def test_network_failure_and_timeout_remain_distinct_without_error_text(self):
        for exception, expected in ((requests.ConnectionError(self.SECRET), "network_error"),
                                    (requests.Timeout(self.TOKEN), "timeout")):
            with self.subTest(expected=expected):
                result, session = self.run_probe([exception])
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["authenticated"])
                self.assertEqual(len(session.calls), 1)

    def test_object_storage_redirect_does_not_receive_bearer_or_basic_auth(self):
        redirect = Response(302, headers={"Location": "https://object-storage.example/data?signature=signed"})
        raw = Response(206, body=self.HDF)
        result, session = self.run_probe([self.auth(), Response(payload=self.catalogue()), redirect, raw])
        self.assertEqual(result["status"], "raw_access_ok")
        _, url, options = session.calls[3]
        self.assertTrue(url.startswith("https://object-storage.example/"))
        self.assertNotIn("Authorization", options["headers"])
        self.assertNotIn("auth", options)
        self.assertEqual(options["headers"]["Range"], "bytes=0-63")
        self.assertTrue(redirect.closed)
        self.assertTrue(raw.closed)

    def test_http_or_userinfo_redirect_is_blocked_before_any_new_request(self):
        for location in ("http://object-storage.example/data", "https://user:password@object-storage.example/data"):
            with self.subTest(location=location):
                redirect = Response(302, headers={"Location": location})
                result, session = self.run_probe([self.auth(), Response(payload=self.catalogue()), redirect])
                self.assertEqual(result["status"], "download_unavailable")
                self.assertEqual(len(session.calls), 3)
                self.assertTrue(redirect.closed)

    def test_redirect_loop_is_bounded_and_closes_every_response(self):
        redirects = [Response(302, headers={"Location": "/still-redirecting"}) for _ in range(3)]
        result, session = self.run_probe([self.auth(), Response(payload=self.catalogue()), *redirects])
        self.assertEqual(result["status"], "download_unavailable")
        self.assertEqual(len(session.calls), 5)
        self.assertTrue(all(response.closed for response in redirects))

    def test_invalid_oauth_token_never_reaches_catalogue(self):
        for token in (None, "", "contains\nnewline", "contains space", 123):
            with self.subTest(token_type=type(token).__name__):
                result, session = self.run_probe([Response(payload={"access_token": token})])
                self.assertEqual(result["status"], "authentication_failed")
                self.assertEqual(len(session.calls), 1)

    def test_malformed_or_oversized_response_does_not_escape_or_print_body(self):
        responses = [Response(body=(self.KEY + self.SECRET + self.TOKEN).encode()),
                     Response(body=b"x" * (probe.JSON_LIMIT + 1))]
        for response in responses:
            with self.subTest(body_length=len(response.body)):
                result, _ = self.run_probe([response])
                self.assertEqual(result["status"], "authentication_failed")
                self.assertTrue(response.closed)

    def test_deadline_prevents_request_after_expiry(self):
        session = Session([])
        with patch.object(probe.time, "monotonic", side_effect=[10.0, 11.0]):
            client = probe.Client(session, seconds=.5)
            with self.assertRaises(probe.ProbeTimeout):
                client.request("GET", probe.SEARCH_URL)
        self.assertEqual(session.calls, [])

    def test_hard_timeout_cleans_up_alarm_and_restores_prior_handler(self):
        previous_handler = object()
        with patch.object(probe.signal, "signal", return_value=previous_handler) as handler, \
                patch.object(probe.signal, "alarm") as alarm:
            with self.assertRaises(probe.ProbeTimeout):
                with probe.hard_timeout(120):
                    installed = handler.call_args.args[1]
                    installed(probe.signal.SIGALRM, None)
            self.assertEqual([call.args for call in alarm.call_args_list], [(120,), (0,)])
            self.assertIs(handler.call_args.args[1], previous_handler)

    def test_exceptions_containing_secrets_do_not_leak_into_cli_or_artifact(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        for exception in (requests.ConnectionError(self.KEY + self.SECRET + self.TOKEN),
                          ValueError(self.KEY + self.SECRET + self.TOKEN)):
            with self.subTest(exception_type=type(exception).__name__), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                session = Session([exception])
                stdout = io.StringIO()
                stderr = io.StringIO()
                with patch.dict(os.environ, {**self.CREDS, "GITHUB_STEP_SUMMARY": str(root / "summary.md")}), \
                        patch.object(probe.requests, "Session", return_value=session), \
                        patch.object(probe, "hard_timeout", return_value=nullcontext()), \
                        patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                    status = probe.main(["--output", str(root / "report.json")])
                self.assertEqual(status, 1)
                serialized = stdout.getvalue() + stderr.getvalue() + (root / "report.json").read_text() + (root / "summary.md").read_text()
                for value in (self.KEY, self.SECRET, self.TOKEN):
                    self.assertNotIn(value, serialized)
                self.assertEqual(stderr.getvalue(), "")
                self.assertTrue(session.closed)

    def test_cli_success_writes_safe_report_and_reads_no_active_app_state(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session = Session([self.auth(), Response(payload=self.catalogue()), Response(206, body=self.HDF)])
            stdout = io.StringIO()
            with patch.dict(os.environ, {**self.CREDS, "GITHUB_STEP_SUMMARY": str(root / "summary.md")}), \
                    patch.object(probe.requests, "Session", return_value=session), \
                    patch.object(probe, "hard_timeout", return_value=nullcontext()), \
                    patch("sys.stdout", stdout):
                status = probe.main(["--output", str(root / "report.json")])
            self.assertEqual(status, 0)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["status"], "raw_access_ok")
            self.assertFalse(report["fire_detector_changed"])
            self.assertEqual({path.name for path in root.iterdir()}, {"report.json", "summary.md"})
            for value in (self.KEY, self.SECRET, self.TOKEN):
                self.assertNotIn(value, stdout.getvalue() + (root / "summary.md").read_text())
            self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()

"""Compare the same raw product through both download paths without exposing secrets."""
from contextlib import nullcontext
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests

from tools import eumetsat_access_probe as old_probe
from tools import eumetsat_download_compare as compare


class ReadTracker(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class Response:
    def __init__(self, status=200, *, payload=None, body=None, headers=None):
        self.status_code = status
        self.body = json.dumps(payload).encode() if body is None else body
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
            raise AssertionError("Unexpected extra request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class DownloadComparisonTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 14, 12, tzinfo=dt.timezone.utc)
    KEY = "offline-consumer-key-sensitive"
    SECRET = "offline-consumer-secret-sensitive"
    TOKEN = "offline-token-sensitive"
    CREDS = {"EUMETSAT_CONSUMER_KEY": KEY, "EUMETSAT_CONSUMER_SECRET": SECRET}
    PRODUCT = "public-product+identifier,with:punctuation"
    ENTRY = "W_XX-EUMETSAT-CHK-BODY-public-example.nc"
    HDF = b"\x89HDF\r\n\x1a\n" + b"h" * 10000
    ZIP = b"PK\x03\x04" + b"z" * 10000
    REASONS = {None, "token_rejected", "insufficient_scope", "licence_required",
               "not_authorised", "gateway_rejection", "unknown"}

    @classmethod
    def auth(cls, **changes):
        return Response(payload={"access_token": cls.TOKEN, "expires_in": 3600, **changes})

    @classmethod
    def catalogue(cls, **changes):
        properties = {"parentIdentifier": old_probe.COLLECTION, "identifier": cls.PRODUCT,
                      "date": "2026-11-14T09:20:00Z/2026-11-14T09:29:59Z",
                      "updated": "2026-11-14T09:35:00Z",
                      "links": {"sip-entries": [{"title": cls.ENTRY,
                                                  "href": "https://untrusted.invalid/steal"}]},
                      **changes}
        return Response(payload={"features": [{"id": properties["identifier"],
                                                "properties": properties}]})

    def run_compare(self, responses, *, creds=None):
        session = Session(responses)
        result = compare.compare_access(self.CREDS if creds is None else creds,
                                        session=session, now=self.NOW)
        encoded = json.dumps(result)
        for secret in (self.KEY, self.SECRET, self.TOKEN):
            self.assertNotIn(secret, encoded)
        self.assertFalse(result.get("fire_detector_changed", False))
        for name in ("entry", "archive"):
            path = result.get(name, {})
            if path:
                self.assertIn(path.get("reason_code"), self.REASONS)
        return result, session

    def test_missing_credentials_cannot_make_requests(self):
        for credentials in ({}, {"EUMETSAT_CONSUMER_KEY": self.KEY}):
            with self.subTest(keys=list(credentials)):
                result, session = self.run_compare([], creds=credentials)
                self.assertEqual(result["status"], "missing_credentials")
                self.assertEqual(session.calls, [])

    def test_authentication_failure_cannot_probe_public_or_private_downloads(self):
        denied = Response(401, body=self.SECRET.encode())
        result, session = self.run_compare([denied])
        self.assertEqual(result["status"], "authentication_failed")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(denied.closed)

    def test_empty_or_wrong_collection_never_reaches_download(self):
        cases = [(Response(payload={"features": []}), "no_products"),
                 (self.catalogue(parentIdentifier="OTHER-COLLECTION"), "catalogue_failed")]
        for catalogue, expected in cases:
            with self.subTest(expected=expected):
                result, session = self.run_compare([self.auth(), catalogue])
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(session.calls), 2)

    def test_both_paths_refer_to_same_product_and_only_trusted_urls_receive_credentials(self):
        result, session = self.run_compare([self.auth(), self.catalogue(),
                                            Response(206, body=self.HDF), Response(206, body=self.ZIP)])
        self.assertEqual(result["status"], "raw_access_ok")
        self.assertEqual(len(session.calls), 4)
        entry_url, archive_url = session.calls[2][1], session.calls[3][1]
        self.assertEqual(urlsplit(entry_url).path, urlsplit(archive_url).path + "/entry")
        entry_query = parse_qs(urlsplit(entry_url).query)
        entry_params = session.calls[2][2].get("params", {})
        self.assertEqual(entry_query.get("name", [entry_params.get("name")]), [self.ENTRY])
        self.assertNotIn("untrusted.invalid", entry_url + archive_url)
        for _, url, options in session.calls[2:]:
            headers = {key.lower(): value for key, value in options["headers"].items()}
            self.assertEqual(headers["range"], "bytes=0-63")
            self.assertEqual(headers["authorization"], "Bearer " + self.TOKEN)
            self.assertNotIn("auth", options)
        self.assertEqual(result["selected_product"]["id"], self.PRODUCT)
        self.assertEqual(result["selected_product"]["entry_name"], self.ENTRY)
        self.assertEqual(result["selected_product"]["sensing_start_utc"], "2026-11-14T09:20:00Z")
        self.assertEqual(result["selected_product"]["sensing_end_utc"], "2026-11-14T09:29:59Z")
        self.assertEqual(result["token_expires_in"], 3600)

    def test_entry_denied_but_archive_zip_proves_archive_only_access(self):
        result, _ = self.run_compare([self.auth(), self.catalogue(),
                                     Response(403, payload={"message": "not authorised"}),
                                     Response(200, body=self.ZIP)])
        self.assertEqual(result["status"], "archive_only_access")
        self.assertEqual(result["entry"]["http_status"], 403)
        self.assertEqual(result["archive"]["payload_kind"], "zip")

    def test_new_or_invalid_sensing_time_cannot_be_mistaken_for_delayed_data_access(self):
        for sensing in ("bad", "2026-11-14T11:10:00Z/2026-11-14T11:19:00Z",
                        "2026-11-15T09:20:00Z/2026-11-15T09:29:00Z"):
            with self.subTest(sensing=sensing):
                result, session = self.run_compare([self.auth(), self.catalogue(date=sensing)])
                self.assertEqual(result["status"], "catalogue_failed")
                self.assertEqual(len(session.calls), 2)
                self.assertFalse(result["entry"]["attempted"])
                self.assertFalse(result["archive"]["attempted"])

    def test_two_rejected_paths_do_not_claim_the_keys_or_licence_are_wrong(self):
        result, session = self.run_compare([self.auth(), self.catalogue(),
                                            Response(403, body=b"Forbidden"),
                                            Response(401, body=b"Unauthorized")])
        self.assertEqual(result["status"], "api_download_denied")
        self.assertTrue(result["authenticated"])
        self.assertEqual(len(session.calls), 4)
        self.assertNotIn("invalid_credentials", json.dumps(result))

    def test_successful_entry_is_enough_even_when_archive_is_denied(self):
        result, _ = self.run_compare([self.auth(), self.catalogue(),
                                     Response(206, body=b"CDF\x02" + b"x" * 100),
                                     Response(403, body=b"Forbidden")])
        self.assertEqual(result["status"], "raw_access_ok")
        self.assertEqual(result["entry"]["payload_kind"], "netcdf")

    def test_http_200_html_is_never_original_data_or_archive_access(self):
        result, _ = self.run_compare([self.auth(), self.catalogue(),
                                     Response(200, body=b"<html>Sign in</html>"),
                                     Response(200, body=b"<html>Access denied</html>")])
        self.assertEqual(result["status"], "download_test_incomplete")
        self.assertEqual(result["entry"]["payload_kind"], "unrecognized")
        self.assertEqual(result["archive"]["payload_kind"], "unrecognized")

    def test_ignored_range_never_reads_the_large_product(self):
        entry, archive = Response(200, body=self.HDF), Response(200, body=self.ZIP)
        result, _ = self.run_compare([self.auth(), self.catalogue(), entry, archive])
        self.assertEqual(entry.raw.read_sizes, [64])
        self.assertEqual(archive.raw.read_sizes, [64])
        self.assertEqual(result["entry"]["bytes_read"], 64)
        self.assertEqual(result["archive"]["bytes_read"], 64)
        self.assertTrue(entry.closed and archive.closed)

    def test_invalid_expiry_and_reflected_public_metadata_do_not_echo_secrets(self):
        result, _ = self.run_compare([
            self.auth(expires_in=self.SECRET),
            self.catalogue(identifier="public-" + self.TOKEN),
            Response(206, body=self.HDF), Response(206, body=self.ZIP)])
        self.assertEqual(result["status"], "raw_access_ok")
        self.assertIsNone(result.get("token_expires_in"))

    def test_error_bodies_are_bounded_and_only_classified(self):
        body = (self.KEY + self.SECRET + self.TOKEN).encode() + b"x" * 20000
        entry, archive = Response(403, body=body), Response(403, body=body)
        result, _ = self.run_compare([self.auth(), self.catalogue(), entry, archive])
        self.assertEqual(result["status"], "api_download_denied")
        for response in (entry, archive):
            self.assertTrue(response.closed)
            self.assertTrue(response.raw.read_sizes)
            self.assertTrue(all(0 <= size <= 4096 for size in response.raw.read_sizes))
            self.assertLessEqual(sum(response.raw.read_sizes), 4096)
        self.assertNotIn("body", result["entry"])
        self.assertNotIn("headers", result["entry"])

    def test_redirected_storage_receives_no_bearer_and_no_basic_credentials(self):
        redirect = Response(302, headers={"Location": "https://storage.example/raw?signature=" + self.SECRET})
        result, session = self.run_compare([self.auth(), self.catalogue(), redirect,
                                            Response(206, body=self.HDF), Response(206, body=self.ZIP)])
        self.assertEqual(result["status"], "raw_access_ok")
        _, url, options = session.calls[3]
        self.assertTrue(url.startswith("https://storage.example/"))
        self.assertNotIn("authorization", {key.lower(): value for key, value in options["headers"].items()})
        self.assertNotIn("auth", options)
        self.assertTrue(redirect.closed)

    def test_unsafe_http_redirect_is_not_followed(self):
        redirect = Response(302, headers={"Location": "http://storage.example/raw"})
        result, session = self.run_compare([self.auth(), self.catalogue(), redirect,
                                            Response(206, body=self.ZIP)])
        self.assertEqual(result["status"], "archive_only_access")
        self.assertTrue(all(url.startswith("https://") for _, url, _ in session.calls))

    def test_expired_global_deadline_prevents_any_request(self):
        session = Session([])
        with patch.object(old_probe.time, "monotonic", side_effect=[10.0, 1000.0]):
            result = compare.compare_access(self.CREDS, session=session, now=self.NOW)
        self.assertEqual(session.calls, [])
        self.assertNotIn(result["status"], ("raw_access_ok", "archive_only_access"))

    def test_download_paths_do_not_reset_the_catalogue_deadline(self):
        clock = [0.0]

        class CatalogueResponse(Response):
            def __exit__(self, *args):
                super().__exit__(*args)
                clock[0] = 121.0

        catalogue = self.catalogue()
        delayed_catalogue = CatalogueResponse(body=catalogue.body)
        session = Session([self.auth(), delayed_catalogue])
        with patch.object(old_probe.time, "monotonic", side_effect=lambda: clock[0]):
            result = compare.compare_access(self.CREDS, session=session, now=self.NOW)
        self.assertEqual(len(session.calls), 2, "Expired downloads must not receive a fresh per-path budget")
        self.assertNotIn(result["status"], ("raw_access_ok", "archive_only_access"))

    def test_exception_text_cannot_expose_credentials_in_report_or_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session([requests.ConnectionError(self.KEY + self.SECRET + self.TOKEN)])
            output = io.StringIO()
            with patch.dict(os.environ, {**self.CREDS, "GITHUB_STEP_SUMMARY": str(root / "summary.md")}), \
                    patch.object(compare.requests, "Session", return_value=session), \
                    patch.object(compare, "hard_timeout", return_value=nullcontext()), \
                    patch("sys.stdout", output):
                code = compare.main(["--output", str(root / "report.json")], now=self.NOW)
            serialized = output.getvalue() + (root / "report.json").read_text() + (root / "summary.md").read_text()
            for secret in (self.KEY, self.SECRET, self.TOKEN):
                self.assertNotIn(secret, serialized)
            self.assertEqual(code, 1)
            self.assertTrue(session.closed)

    def test_cli_archive_only_success_retains_the_two_distinct_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session([self.auth(), self.catalogue(), Response(403, body=b"Forbidden"),
                               Response(206, body=self.ZIP)])
            with patch.dict(os.environ, self.CREDS), \
                    patch.object(compare.requests, "Session", return_value=session), \
                    patch.object(compare, "hard_timeout", return_value=nullcontext()), \
                    patch("sys.stdout", io.StringIO()):
                code = compare.main(["--output", str(root / "report.json")], now=self.NOW)
            result = json.loads((root / "report.json").read_text())
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "archive_only_access")
            self.assertEqual(result["entry"]["http_status"], 403)
            self.assertEqual(result["archive"]["payload_kind"], "zip")
            self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()

"""Check EUMETSAT HRFI access without changing Rasad or revealing credentials.

Authenticate, search one older product and read at most 64 bytes of one NetCDF
entry. This checks access, not fire detection or a complete file's calibration.
Official API definitions: https://gitlab.eumetsat.int/eumetlab/data-services/eumdac
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import json
import logging
import os
from pathlib import Path
import signal
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit

import requests

COLLECTION = "EO:EUM:DAT:0665"
TOKEN_URL = "https://api.eumetsat.int/token"
SEARCH_URL = "https://api.eumetsat.int/data/search-products/1.0.0/os"
SAMPLE_BYTES = 64
JSON_LIMIT = 1024 * 1024
MESSAGES = {
    "missing_credentials": "لم يُعثر على المفتاحين في GitHub Secrets بالأسماء المطلوبة.",
    "authentication_failed": "لم تنجح المصادقة. راجع قيم المفتاحين وحالة الحساب.",
    "catalogue_failed": "نجحت المصادقة، وتعذر استرجاع فهرس البيانات في هذه المحاولة.",
    "no_products": "نجحت المصادقة والاستعلام، ولم تتوفر منتجات في النافذة المختبرة.",
    "catalogue_only": "نجحت المصادقة والاستعلام، ولم يتوفر ملف NetCDF مناسب لاختبار التنزيل.",
    "download_denied": "المفتاحان صالحان، لكن تنزيل العينة رُفض. راجع صلاحية المجموعة والترخيص في حساب EUMETSAT.",
    "download_unavailable": "المفتاحان صالحان، لكن العينة غير متاحة حالياً. هذا لا يحدد وحده سبب التعذر.",
    "unexpected_payload": "وصلت استجابة، لكنها لا تحتوي ترويسة NetCDF المتوقعة. لم يُثبت الوصول للبيانات الأصلية.",
    "raw_access_ok": "نجحت المصادقة وقراءة ترويسة عينة NetCDF أصلية. هذا فحص وصول فقط؛ كاشف الحرائق لم يتغير.",
    "timeout": "انتهت مهلة فحص الاتصال. لم تتغير مراقبة الحرائق.",
    "network_error": "تعذر الاتصال بخدمة EUMETSAT. هذه النتيجة لا تعني أن المفتاحين غير صالحين.",
    "service_unavailable": "خدمة EUMETSAT مشغولة أو متعذرة مؤقتاً. أعد الفحص لاحقاً.",
    "probe_failed": "تعذر إكمال فحص الاتصال. لم تتغير مراقبة الحرائق.",
}


class ProbeFailure(Exception):
    def __init__(self, http_status=None):
        self.http_status = http_status


class ProbeTimeout(Exception):
    pass


def stamp(when):
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def hard_timeout(seconds):
    """The Actions runner is Linux; its job timeout is a second outer bound."""
    def expired(signum, frame):
        raise ProbeTimeout()

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class Client:
    def __init__(self, session, seconds=120):
        self.session = session
        self.deadline = time.monotonic() + seconds

    def request(self, method, url, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeTimeout()
        return self.session.request(method, url, timeout=min(20, remaining),
                                    allow_redirects=False, stream=True, **kwargs)

    def json(self, method, url, **kwargs):
        with self.request(method, url, **kwargs) as response:
            if response.status_code != 200:
                raise ProbeFailure(response.status_code)
            body = bytearray()
            for block in response.iter_content(chunk_size=8192):
                if time.monotonic() > self.deadline:
                    raise ProbeTimeout()
                body.extend(block)
                if len(body) > JSON_LIMIT:
                    raise ProbeFailure()
            return json.loads(body)

    def sample(self, url, token):
        # A download can redirect to object storage. Only the official API host
        # receives the bearer; redirected services never receive that credential.
        for _ in range(3):
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
                raise ProbeFailure()
            headers = {"Range": f"bytes=0-{SAMPLE_BYTES - 1}", "Accept-Encoding": "identity"}
            if parts.netloc == "api.eumetsat.int":
                headers["Authorization"] = "Bearer " + token
            with self.request("GET", url, headers=headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise ProbeFailure(response.status_code)
                    url = urljoin(url, location)
                    continue
                if response.status_code not in (200, 206):
                    raise ProbeFailure(response.status_code)
                # A server may ignore Range: never read the complete entry.
                return response.raw.read(SAMPLE_BYTES)
        raise ProbeFailure()


def check_access(environ=None, session=None, now=None):
    environ = os.environ if environ is None else environ
    now = now or dt.datetime.now(dt.timezone.utc)
    end = now - dt.timedelta(hours=2)
    start = end - dt.timedelta(hours=1)
    report = {"status": "probe_failed", "phase": "configuration",
              "collection": COLLECTION, "checked_utc": stamp(now),
              "search_start_utc": stamp(start), "search_end_utc": stamp(end),
              "authenticated": False, "catalogue_ok": False, "raw_sample_ok": False,
              "bytes_read": 0, "fire_detector_changed": False}
    key = environ.get("EUMETSAT_CONSUMER_KEY", "").strip()
    secret = environ.get("EUMETSAT_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        report["status"] = "missing_credentials"
        return report
    owned_session = session is None
    session = session or requests.Session()
    client = Client(session)
    try:
        report["phase"] = "authentication"
        report["status"] = "authentication_failed"
        auth = client.json("POST", TOKEN_URL, auth=(key, secret),
                           data={"grant_type": "client_credentials"})
        token = auth.get("access_token")
        if (not isinstance(token, str) or not token
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise ProbeFailure()
        report["authenticated"] = True
        report["phase"] = "catalogue"
        report["status"] = "catalogue_failed"
        catalogue = client.json("GET", SEARCH_URL, headers={"Authorization": "Bearer " + token},
                                params={"format": "json", "pi": COLLECTION, "c": 1, "si": 0,
                                        "dtstart": stamp(start), "dtend": stamp(end)})
        features = catalogue.get("features")
        if not isinstance(features, list):
            raise ProbeFailure()
        report["catalogue_ok"] = True
        if not features:
            report["status"] = "no_products"
            return report
        properties = features[0]["properties"]
        identifier = properties["identifier"]
        if properties.get("parentIdentifier") != COLLECTION or not isinstance(identifier, str):
            raise ProbeFailure()
        entries = properties.get("links", {}).get("sip-entries", [])
        filename = next((item.get("title") for item in entries
                         if isinstance(item.get("title"), str)
                         and "CHK-BODY" in item["title"] and item["title"].endswith(".nc")), None)
        if not filename:
            report["status"] = "catalogue_only"
            return report
        # Construct a trusted API URL; do not forward credentials to catalogue hrefs.
        url = ("https://api.eumetsat.int/data/download/1.0.0/collections/"
               + quote(COLLECTION, safe="") + "/products/" + quote(identifier, safe="")
               + "/entry?" + urlencode({"name": filename}))
        report["phase"] = "download"
        report["status"] = "download_unavailable"
        sample = client.sample(url, token)
        report["bytes_read"] = len(sample)
        valid = (sample.startswith(b"\x89HDF\r\n\x1a\n")
                 or sample.startswith((b"CDF\x01", b"CDF\x02", b"CDF\x05")))
        report["raw_sample_ok"] = valid
        report["status"] = "raw_access_ok" if valid else "unexpected_payload"
        return report
    except ProbeTimeout:
        report["status"] = "timeout"
    except ProbeFailure as exc:
        if isinstance(exc.http_status, int):
            report["http_status"] = exc.http_status
            if exc.http_status == 429 or exc.http_status >= 500:
                report["status"] = "service_unavailable"
            elif report["phase"] == "download" and exc.http_status in (401, 403):
                report["status"] = "download_denied"
    except requests.Timeout:
        report["status"] = "timeout"  # no exception text: it may contain credentials
    except requests.ConnectionError:
        report["status"] = "network_error"
    except Exception:
        pass  # preserve the safe phase/status without response bodies or tracebacks
    finally:
        if owned_session:
            session.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="eumetsat-check/report.json")
    args = parser.parse_args(argv)
    # Requests debugging can expose authentication headers. Emit whitelisted fields only.
    logging.disable(logging.CRITICAL)
    try:
        with hard_timeout(120):
            report = check_access()
    except ProbeTimeout:
        report = {"status": "timeout", "phase": "unknown", "raw_sample_ok": False,
                  "fire_detector_changed": False}
    except Exception:
        report = {"status": "probe_failed", "phase": "unknown", "raw_sample_ok": False,
                  "fire_detector_changed": False}
    report["message_ar"] = MESSAGES[report["status"]]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write("### فحص الوصول إلى EUMETSAT\n\n" + report["message_ar"]
                       + "\n\nالحالة: `" + report["status"] + "`\n")
    return 0 if report["status"] == "raw_access_ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

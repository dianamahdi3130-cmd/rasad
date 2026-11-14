"""Compare bounded entry and archive reads of the same older EUMETSAT product.

Only public product metadata and fixed result/reason codes leave this diagnostic.
It does not modify credentials, media, detections or application state.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import logging
import math
import os
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlsplit
import requests
from tools.eumetsat_access_probe import (
    COLLECTION, SEARCH_URL, TOKEN_URL, Client, ProbeFailure, ProbeTimeout, hard_timeout, stamp,
)

SAMPLE_BYTES = 64
ERROR_BYTES = 4096
MESSAGES = {
    "raw_access_ok": "نجحت قراءة عينة NetCDF أصلية عبر الاتصال البرمجي. هذا فحص وصول فقط.",
    "archive_only_access": "نجحت قراءة عينة الأرشيف برمجياً، لكن قراءة ملف NetCDF منفرداً لم تنجح.",
    "api_download_denied": "نجحت المصادقة، ورُفض مسارا التنزيل البرمجي للمنتج نفسه. سبب الصلاحية يحتاج متابعة.",
    "download_test_incomplete": "لم تكتمل مقارنة مساري التنزيل بنتيجة صالحة؛ راجع نتيجة كل مسار.",
    "missing_credentials": "أحد المفتاحين غير متاح في أسرار GitHub بالأسماء المحددة.",
    "authentication_failed": "لم تنجح المصادقة بالمفتاحين في هذه المحاولة.",
    "catalogue_failed": "تعذر استرجاع منتج مناسب من الفهرس أو التحقق من بياناته.",
    "no_products": "نجحت المصادقة، ولم يتوفر منتج في نافذة البحث المختبرة.",
    "catalogue_only": "وصل الفهرس دون ملف NetCDF مناسب لاختبار مسار الملف المنفرد.",
}


def _path_result():
    return {"attempted": False, "http_status": None, "bytes_read": 0,
            "payload_kind": None, "reason_code": None}


def _public_text(value, redactions):
    if not isinstance(value, str):
        return None
    for secret in redactions:
        value = value.replace(secret, "[redacted]") if secret else value
    return "".join(char for char in value if char.isprintable())[:512]


def _reason(response, redactions):
    # Inspect a bounded error body, returning an enum instead of arbitrary text.
    body = response.raw.read(ERROR_BYTES).decode("utf-8", errors="replace")
    text = body + " " + response.headers.get("WWW-Authenticate", "")
    for secret in redactions:
        text = text.replace(secret, "") if secret else text
    text = text.lower()
    if "invalid_token" in text or "token expired" in text:
        return "token_rejected"
    if "insufficient_scope" in text:
        return "insufficient_scope"
    if "licen" in text and any(word in text for word in ("required", "missing", "invalid", "accept")):
        return "licence_required"
    if "cloudflare" in text or "web application firewall" in text:
        return "gateway_rejection"
    return "not_authorised" if response.status_code in (401, 403) else "unknown"


def _sample(client, url, token, redactions):
    result = _path_result()
    for _ in range(3):
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 443)):
            result["reason_code"] = "gateway_rejection"
            return result
        headers = {"Range": "bytes=0-63", "Accept-Encoding": "identity"}
        if parts.netloc == "api.eumetsat.int":
            headers["Authorization"] = "Bearer " + token
        result["attempted"] = True
        try:
            with client.request("GET", url, headers=headers) as response:
                result["http_status"] = response.status_code
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        result["reason_code"] = "gateway_rejection"
                        return result
                    url = urljoin(url, location)
                    continue
                if response.status_code not in (200, 206):
                    result["reason_code"] = _reason(response, redactions)
                    return result
                sample = response.raw.read(SAMPLE_BYTES)
                result["bytes_read"] = len(sample)
                if sample.startswith((b"\x89HDF\r\n\x1a\n", b"CDF\x01", b"CDF\x02", b"CDF\x05")):
                    result["payload_kind"] = "netcdf"
                elif sample.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                    result["payload_kind"] = "zip"
                else:
                    result["payload_kind"] = "unrecognized"
                return result
        except (ProbeTimeout, requests.RequestException, ValueError):
            result["reason_code"] = "unknown"
            return result
    result["reason_code"] = "gateway_rejection"
    return result


def _date(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        return parsed.astimezone(dt.timezone.utc) if parsed and parsed.tzinfo else None
    except ValueError:
        return None


def compare_access(environ=None, session=None, now=None):
    environ = os.environ if environ is None else environ
    now = now or dt.datetime.now(dt.timezone.utc)
    end, start = now - dt.timedelta(hours=2), now - dt.timedelta(hours=3)
    report = {"status": "missing_credentials", "phase": "configuration", "checked_utc": stamp(now),
              "collection": COLLECTION, "search_start_utc": stamp(start), "search_end_utc": stamp(end),
              "authenticated": False, "catalogue_ok": False, "token_expires_in": None,
              "selected_product": None, "entry": _path_result(), "archive": _path_result(),
              "fire_detector_changed": False}
    key = environ.get("EUMETSAT_CONSUMER_KEY", "").strip()
    secret = environ.get("EUMETSAT_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        return report
    redactions = [key, secret]
    owned = session is None
    session = session or requests.Session()
    client = Client(session)
    try:
        report.update(status="authentication_failed", phase="authentication")
        auth = client.json("POST", TOKEN_URL, auth=(key, secret), data={"grant_type": "client_credentials"})
        token = auth.get("access_token")
        if (not isinstance(token, str) or not token
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            return report
        redactions.append(token)
        report["authenticated"] = True
        expiry = auth.get("expires_in")
        if not isinstance(expiry, bool):
            try:
                seconds = float(expiry)
                if math.isfinite(seconds) and 0 <= seconds <= 7 * 24 * 3600:
                    report["token_expires_in"] = int(seconds)
            except (TypeError, ValueError, OverflowError):
                pass
        report.update(status="catalogue_failed", phase="catalogue")
        catalogue = client.json("GET", SEARCH_URL, headers={"Authorization": "Bearer " + token},
                                params={"format": "json", "pi": COLLECTION, "c": 1, "si": 0,
                                        "dtstart": stamp(start), "dtend": stamp(end)})
        features = catalogue.get("features")
        if not isinstance(features, list):
            return report
        report["catalogue_ok"] = True
        if not features:
            report["status"] = "no_products"
            return report
        properties = features[0]["properties"]
        identifier = properties.get("identifier")
        if properties.get("parentIdentifier") != COLLECTION or not isinstance(identifier, str):
            return report
        entries = properties.get("links", {}).get("sip-entries", [])
        filename = next((row.get("title") for row in entries if isinstance(row, dict)
                         and isinstance(row.get("title"), str) and "CHK-BODY" in row["title"]
                         and row["title"].endswith(".nc")), None)
        if not filename:
            report["status"] = "catalogue_only"
            return report
        public = {"id": _public_text(identifier, redactions), "entry_name": _public_text(filename, redactions)}
        sensing = properties.get("date", "")
        bounds = sensing.split("/") if isinstance(sensing, str) else []
        t0, t1 = (_date(bounds[0]), _date(bounds[1])) if len(bounds) == 2 else (None, None)
        report["selected_product"] = public
        if not (t0 and t1 and t0 <= t1 <= now - dt.timedelta(hours=1)
                and t0 <= end and t1 >= start):
            return report  # a recent or unknown time must not test a pending fast-data licence
        public.update(sensing_start_utc=stamp(t0), sensing_end_utc=stamp(t1),
                      sensing_end_age_minutes=round((now - t1).total_seconds() / 60, 1))
        archive = ("https://api.eumetsat.int/data/download/1.0.0/collections/"
                   + quote(COLLECTION, safe="") + "/products/" + quote(identifier, safe=""))
        entry = archive + "/entry?" + urlencode({"name": filename})
        report.update(status="download_test_incomplete", phase="download")
        report["entry"] = _sample(client, entry, token, redactions)
        report["archive"] = _sample(client, archive, token, redactions)
        if report["entry"]["payload_kind"] == "netcdf":
            report["status"] = "raw_access_ok"
        elif report["archive"]["payload_kind"] == "zip":
            report["status"] = "archive_only_access"
        elif all(report[name]["http_status"] in (401, 403) for name in ("entry", "archive")):
            report["status"] = "api_download_denied"
    except (ProbeFailure, ProbeTimeout, requests.RequestException, ValueError, KeyError, TypeError):
        pass
    finally:
        if owned:
            session.close()
    return report


def main(argv=None, *, now=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="eumetsat-compare/report.json")
    args = parser.parse_args(argv)
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with hard_timeout(120):
            report = compare_access(now=now)
    except Exception:
        report = {"status": "download_test_incomplete", "fire_detector_changed": False}
    finally:
        logging.disable(previous_logging)
    report["message_ar"] = MESSAGES[report["status"]]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("### مقارنة مساري تنزيل EUMETSAT\n\n" + report["message_ar"] + "\n\n")
            for name in ("entry", "archive"):
                result = report.get(name, {})
                stream.write(f"- {name}: HTTP {result.get('http_status')}، "
                             f"نوع العينة {result.get('payload_kind')}، "
                             f"السبب المصنّف {result.get('reason_code')}\n")
    return 0 if report["status"] in ("raw_access_ok", "archive_only_access") else 1


if __name__ == "__main__":
    raise SystemExit(main())

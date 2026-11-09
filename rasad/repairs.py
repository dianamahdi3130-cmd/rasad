"""Quiet, fingerprint-guarded repairs of explicitly reviewed historical records.

The bundled observations are public NASA captures with retained provenance. A
repair may reconstruct acquisition records; it does not identify a cause or
establish that two acquisition groups are two separate physical fires.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from . import acquisitions
from .brain import add_to_incident, finalize, new_incident
from .util import iso, log, parse_time

_CASES = {"hormuz_20261108": {
    "old_id": "20261108-0841_26.103_55.884",
    "first": "2026-11-08T08:41:00Z", "last": "2026-11-08T17:02:00Z",
    "morning_last": "2026-11-08T10:21:00Z",
    "observations_sha256": "af5c8c4ac5b857fa9a920247dbd17b476f2591c707680b0f66f3af3e5d40c599",
}}
_SOURCES = frozenset({"VIIRS_NOAA20_NRT", "MODIS_NRT"})
_POST_FIELDS = ("tg_msg", "alerted_at", "alerted_verdict", "alerted_max_frp", "alert_updated_at")
_PLACE_FIELDS = ("place", "where", "country", "facility", "region_name", "regions", "_pos",
                 "known_days", "typical_frp", "area_days", "near_confirmed")
_CLIP_FIELDS = ("clip", "snap", "clip_max_frp", "clip_peak_time", "clip_style", "clip_updated_at")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _fixture(case):
    path = Path(__file__).resolve().parent / "reviews" / (case + ".json")
    reviewed = json.loads(path.read_text(encoding="utf-8"))
    rows = reviewed.get("observations")
    expected = _CASES[case]
    if (reviewed.get("schema") != 1 or reviewed.get("id") != case
            or reviewed.get("old_incident_id") != expected["old_id"]
            or not isinstance(rows, list) or len(rows) != 10
            or _digest(rows) != expected["observations_sha256"]
            or reviewed.get("observations_sha256") != expected["observations_sha256"]):
        raise ValueError("reviewed observations failed their fingerprint")
    if not reviewed.get("sources"):
        raise ValueError("reviewed observations have no source provenance")
    return reviewed


def _legacy_record(inc, expected):
    """Return a refusal reason; an empty string means the exact legacy record matches."""
    if inc.get("first") != expected["first"] or inc.get("last") != expected["last"]:
        return "acquisition window changed"
    if inc.get("kind") != "new" or inc.get("best") != "polar":
        return "incident type changed"
    if set(inc.get("srcs") or []) != _SOURCES or set(inc.get("families") or []) != {"viirs", "modis"}:
        return "source membership changed"
    if inc.get("observations") or inc.get("geo_slots"):
        return "new positioned or geostationary evidence exists"
    try:
        if (not math.isfinite(float(inc["max_frp"])) or abs(float(inc["max_frp"]) - 47.42) > 0.05
                or abs(float(inc["lat"]) - 26.1034) > 0.0002
                or abs(float(inc["lon"]) - 55.8839) > 0.0002):
            return "legacy strength or anchor changed"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "legacy strength or anchor missing"
    allowed = {expected["first"]: (13.3,), expected["morning_last"]: (26.37, 26.4),
               expected["last"]: (47.42, 47.4, 21.34, 21.3)}
    polar = inc.get("polar")
    if not isinstance(polar, list) or not polar:
        return "legacy acquisition readings missing"
    for row in polar:
        if not isinstance(row, dict) or any(key in row for key in ("lat", "lon")):
            return "legacy acquisition positions changed"
        try:
            permitted = allowed.get(row.get("t"), ())
            if not any(abs(float(row["frp"]) - power) <= 0.05 for power in permitted):
                return "legacy acquisition readings changed"
        except (KeyError, TypeError, ValueError, OverflowError):
            return "legacy acquisition readings invalid"
    if {row.get("t") for row in polar} != set(allowed):
        return "legacy acquisition readings incomplete"
    for row in inc.get("frp_series") or []:
        try:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                return "legacy strength series invalid"
            permitted = allowed.get(row[0], ())
            if not any(abs(float(row[1]) - power) <= 0.05 for power in permitted):
                return "legacy strength series changed"
        except (TypeError, ValueError, OverflowError):
            return "legacy strength series invalid"
    return ""


def _rebuild(rows, cfg, original, expected):
    """Build every acquisition atomically before touching the live state list."""
    rebuilt = []
    merge = cfg["incidents"]
    for group in acquisitions.groups(copy.deepcopy(rows), float(merge.get("merge_km", 6))):
        inc = acquisitions.find_group(rebuilt, group, float(merge.get("merge_km", 6)),
                                      float(merge.get("merge_hours", 12)), rules=merge)
        if inc is None:
            inc = new_incident(group[0], "new")
            # The old public URL and thread belong to the morning acquisition record.
            if group[0]["t"] == expected["first"]:
                inc["id"] = expected["old_id"]
            rebuilt.append(inc)
        for det in group:
            add_to_incident(inc, det)
    morning = next((i for i in rebuilt if i["id"] == expected["old_id"]), None)
    evening = next((i for i in rebuilt if i["first"] == expected["last"]), None)
    if (len(rebuilt) != 2 or morning is None or evening is None
            or len(morning.get("observations") or []) != 6
            or len(evening.get("observations") or []) != 4
            or morning["last"] != expected["morning_last"]
            or abs(morning["max_frp"] - 61.07) > 0.001
            or abs(evening["max_frp"] - 50.59) > 0.001
            or not evening.get("association_uncertain")
            or evening.get("association", {}).get("previous_id") != expected["old_id"]):
        raise ValueError("reviewed acquisitions no longer reproduce the reviewed partition")

    for key in _PLACE_FIELDS:
        if key in original:
            morning[key] = copy.deepcopy(original[key])
    for key in _POST_FIELDS:
        if key in original:
            morning[key] = copy.deepcopy(original[key])
    morning["status"] = original.get("status", "new")
    # A 10:21 film showing the actual old 26.37-MW measurement remains useful
    # until its stronger reviewed peak can be rendered. An evening film cannot
    # be reassigned to the morning acquisition merely because its filename matches.
    try:
        keep_media = (original.get("clip_peak_time") == expected["morning_last"]
                      and abs(float(original.get("clip_max_frp")) - 26.37) <= 0.05)
    except (TypeError, ValueError, OverflowError):
        keep_media = False
    if keep_media:
        for key in _CLIP_FIELDS:
            value = original.get(key)
            if key in {"clip", "snap"}:
                part = Path(value) if isinstance(value, str) else Path()
                if part.is_absolute() or len(part.parts) != 2 or part.parts[0] != "clips" or ".." in part.parts:
                    continue
            if key in original:
                morning[key] = copy.deepcopy(value)
    morning["clip_needs_refresh"] = True
    morning["correction_note"] = ("أعيد بناء الرصد الصباحي من بكسلات ناسا الأصلية بعد إصلاح حذف بكسلات متجاورة؛ "
                                    "القمة الصباحية 61.07 ميغاواط. الرصد المسائي محفوظ للمطابقة في بطاقة أخرى.")
    evening.update(place=original.get("place", "sea"), facility=None, country="",
                   region_name=original.get("region_name"), regions=copy.deepcopy(original.get("regions") or []),
                   where="رصد مسائي في البحر قرب مضيق هرمز", known_days=original.get("known_days", 0),
                   area_days=original.get("area_days", 0))
    evening["correction_note"] = ("رصد مسائي أعيدت مراجعته من ناسا؛ فُصل في السجل للمطابقة، "
                                    "ولم يثبت أنه حريق مستقل أو استمرار للرصد الصباحي.")
    for inc in rebuilt:
        inc["recovered_historical"] = True
        inc["clip_video_pending"] = False
        inc["historical_correction"] = "hormuz_20261108"
        finalize(inc, cfg)
    return rebuilt


def apply_reviewed(cfg, S, now):
    """Apply enabled reviewed cases quietly, once, after validating the old record.

    This function performs no network, Telegram delivery or disk state writes.
    The scanner's normal save commits the replacement and its original audit copy.
    """
    options = cfg.get("repairs") or {}
    if not options.get("enabled"):
        return 0
    now = parse_time(now)
    count = 0
    for case in options.get("cases") or []:
        if case not in _CASES:
            continue
        expected = _CASES[case]
        originals = [i for i in S.incidents if i.get("id") == expected["old_id"]]
        if not originals:
            continue  # never create an event merely because a repair was enabled
        audit = S.state.setdefault("reviewed_repairs", {})
        previous = audit.get(case) or {}
        original = originals[0]
        refusal = _legacy_record(original, expected)
        # State.save writes its state marker before the incident list. Resume a
        # matching legacy record if a restart happened between those atomic files.
        if previous.get("status") == "applied" and refusal:
            continue
        if len(originals) != 1:
            refusal = "duplicate target incident IDs"
        if now < parse_time(expected["last"]):
            refusal = "reviewed acquisition is in the future"
        elif now - parse_time(expected["last"]) > dt.timedelta(days=float(cfg["incidents"].get("keep_days", 7))):
            refusal = "reviewed acquisition is outside retention"
        if not refusal:
            try:
                reviewed = _fixture(case)
                rebuilt = _rebuild(reviewed["observations"], cfg, original, expected)
                replacement_ids = {i["id"] for i in rebuilt}
                if any(i.get("id") in replacement_ids and i is not original for i in S.incidents):
                    refusal = "a replacement incident already exists"
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
                refusal = type(exc).__name__ + ": reviewed evidence could not be reproduced"
        if refusal:
            fingerprint = _digest(original)
            if previous.get("fingerprint") != fingerprint or previous.get("reason") != refusal:
                audit[case] = {"status": "review_needed", "reason": refusal,
                               "fingerprint": fingerprint, "checked_at": iso(now)}
                log.warning("reviewed repair %s deferred: %s", case, refusal)
            continue
        before = copy.deepcopy((previous.get("original") or original)
                               if previous.get("status") == "applied" else original)
        position = S.incidents.index(original)
        updated = list(S.incidents)
        updated[position:position + 1] = rebuilt
        audit[case] = {"status": "applied", "applied_at": iso(now), "original": before,
                       "observations_sha256": reviewed["observations_sha256"],
                       "replacement_ids": [i["id"] for i in rebuilt],
                       "notification_policy": "historical only; no new alerts or pending videos"}
        S.incidents = updated
        count += 1
        log.info("reviewed repair %s rebuilt two acquisition records without sending alerts", case)
    return count

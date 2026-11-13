"""Early night observations, separate from the established fire incident ledger.

The first accepted night spot can reach the channel immediately. Its uncalibrated
display index is deliberately absent here. A frozen media snapshot lets the worker
finish the first video while later observations update the public preview.
"""
from __future__ import annotations

import copy
import datetime as dt
from html import escape
import math
from pathlib import Path

from . import mediaqueue, preliminary
from .brain import app_link
from .util import haversine_km, iso, local_text, parse_time

DEFAULTS = {"enabled": False, "max_age_min": 45, "max_new_per_run": 3,
            "match_km": 1.5, "match_minutes": 40, "keep_days": 7}
_PUBLIC = ("id", "kind", "preliminary", "headline", "where", "place", "country", "facility",
           "first", "last", "lat", "lon", "best", "start_text", "last_text", "verdict", "priority",
           "size", "reason", "max_frp", "frp_series", "clip", "snap", "clip_note", "clip_status_text",
           "sources_text", "regions", "region_name")


def _settings(cfg):
    return {**DEFAULTS, **cfg.get("seawatch", {}).get("early_alerts", {})}


def enabled(cfg):
    sw = cfg.get("seawatch", {})
    return bool(sw.get("enabled") and sw.get("alerts") and _settings(cfg).get("enabled"))


def _number(value, default, minimum=0):
    try:
        result = float(value)
        return max(minimum, result) if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def _time(value):
    try:
        return parse_time(value)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def _identity(row):
    pos, when = preliminary._position(row), _time(row.get("t"))
    if row.get("src") != preliminary.SOURCE or pos is None or when is None:
        return None
    return preliminary.SOURCE, iso(when), round(pos[0], 4), round(pos[1], 4)


def _origin(item):
    return {"src": preliminary.SOURCE, "t": item["first"], "lat": item["lat"], "lon": item["lon"]}


def _lifetime(cfg):
    return dt.timedelta(hours=_number(cfg.get("seawatch", {}).get("catchup_hours", 3), 3) + 1)


def _store(state):
    if not isinstance(state.get("early_night"), dict):
        state["early_night"] = {}
    return state["early_night"]


def _incident_for(record, incidents, settings):
    """Require the originating source AND acquisition, never mere incident proximity.

    The finder retains its coordinates when emitting a confirmed pair, so prefer
    that exact provenance. A lone small rounding difference is accepted only when
    no different same-acquisition observation makes the association ambiguous.
    """
    origin = record.get("origin") or {}
    identity = _identity(origin)
    if identity is None:
        return None
    exact, close = [], []
    reach = _number(settings.get("match_km"), 1.5)
    for incident in incidents:
        distances = []
        is_exact = False
        for observation in incident.get("observations") or []:
            if not isinstance(observation, dict):
                continue
            key = _identity(observation)
            if key is None or key[:2] != identity[:2]:
                continue
            distance = haversine_km(origin["lat"], origin["lon"], observation["lat"], observation["lon"])
            if distance <= reach:
                distances.append(distance)
                is_exact = is_exact or key == identity
        if is_exact:
            exact.append(incident)
        elif distances and min(distances) <= .03:
            # JSON/reprojection rounding is much smaller than a satellite pixel.
            # The full matching radius is for subsequent acquisitions, not a
            # licence to claim a different nearby origin as this first spot.
            close.append(incident)
    choices = exact or close
    return choices[0] if len(choices) == 1 else None


def _promote(records, incidents, settings, now):
    matches = []
    for record in records.values():
        if record.get("promoted_to"):
            continue
        incident = _incident_for(record, incidents, settings)
        if incident is not None:
            matches.append((record, incident))
    for record, incident in matches:
        # More than one distinct origin represented by one incident is ambiguous;
        # do not overwrite one channel thread with another channel thread.
        record["promoted_to"], record["promoted_at"] = incident["id"], iso(now)
        aliases = incident.setdefault("early_ids", [])
        if record["id"] not in aliases:
            aliases.append(record["id"])
        if len(aliases) > 1 or sum(other is incident for _, other in matches) != 1:
            incident["early_promotion_ambiguous"] = True
            record["promotion_ambiguous"] = True
            continue
        already_alerted = (incident.get("alerted_verdict") is not None or incident.get("alerted_at")
                           or incident.get("tg_msg") or incident.get("status") == "alerted")
        if not record.get("sent_at") or already_alerted:
            continue
        if record.get("tg_msg") is not None:
            incident["tg_msg"] = record["tg_msg"]
        incident.update(alerted_at=record["sent_at"], alerted_verdict="يحتاج تأكيداً", status="alerted",
                        alerted_max_frp=None, alert_updated_at=record["sent_at"])
        _share_media(record, incidents)


def _same_record(records, origin, settings):
    key = _identity(origin)
    for record in records.values():
        if any(_identity(row) == key for row in record.get("observations") or []):
            return record
    choices = []
    minutes = _number(settings.get("match_minutes"), 40)
    reach = _number(settings.get("match_km"), 1.5)
    at = _time(origin["t"])
    for record in records.values():
        if record.get("promoted_to"):
            continue
        anchor = record.get("origin") or {}
        anchor_at = _time(anchor.get("t"))
        if anchor_at is None or preliminary._position(anchor) is None:
            continue
        # Different spots in the SAME image remain different early records.
        if any(_time(row.get("t")) == at for row in record.get("observations") or []):
            continue
        if abs((at - anchor_at).total_seconds()) > minutes * 60:
            continue
        if haversine_km(origin["lat"], origin["lon"], anchor["lat"], anchor["lon"]) <= reach:
            choices.append(record)
    return choices[0] if len(choices) == 1 else None


def sync(cfg, S, now):
    """Ingest fresh accepted night spots and reconcile exact real evidence. No sends.

    Only promotion metadata is written to an existing genuine incident. Candidate
    observations never enter the incident ledger or the usual-heat memory.
    """
    if not enabled(cfg):
        return 0
    settings = _settings(cfg)
    records = _store(S.state)
    keep = dt.timedelta(days=_number(settings.get("keep_days"), 7))
    for key, record in list(records.items()):
        last = _time(record.get("last")) if isinstance(record, dict) else None
        if last is None or now - last > keep:
            if isinstance(record, dict):
                snapshot = record.get("media_snapshot") or {}
                mediaqueue.discard(cfg, snapshot.get("media_job"))
            del records[key]
    _promote(records, S.incidents, settings, now)
    max_age = dt.timedelta(minutes=_number(settings.get("max_age_min"), 45))
    original = {_identity(row): row for row in S.state.get("sea_candidates") or []
                if isinstance(row, dict) and _identity(row) is not None}
    added = 0
    # Oldest first chooses a stable anchor when several delayed slots arrive together.
    previews = preliminary.public_candidates(cfg, S.state, [], now)
    for item in sorted(previews, key=lambda row: (row["first"], row["id"])):
        if now - parse_time(item["first"]) > max_age:
            continue
        origin = _origin(item)
        # Already established before any early message: use the normal alert path.
        if _incident_for({"origin": origin}, S.incidents, settings) is not None:
            continue
        record = _same_record(records, origin, settings)
        if record is not None:
            if _identity(origin) not in {_identity(row) for row in record["observations"]}:
                record["observations"].append(origin)
                record["observations"] = sorted(record["observations"], key=lambda row: row["t"])[-60:]
                record["last"] = max(record["last"], item["last"])
                record["last_text"] = local_text(parse_time(record["last"]), cfg)
            continue
        record = copy.deepcopy(item)
        record.update(origin=origin, observations=[origin], srcs=[preliminary.SOURCE], families=["mtg"],
                      geo_slots=[origin["t"]], polar=[], created_at=iso(now))
        row = original.get(_identity(origin)) or {}
        for field in ("source_grid", "reference_minutes"):
            if row.get(field) is not None:
                record[field] = copy.deepcopy(row[field])
        record.pop("clip_note", None)
        record["clip_status_text"] = "يجري تجهيز فيديو الرصد الحراري الأولي"
        records[record["id"]] = record
        added += 1
    return added


def _snapshot(record):
    if not isinstance(record.get("media_snapshot"), dict):
        # Freeze initial image time and coordinates. New acquisitions must not
        # continually supersede the very video that is still being rendered.
        record["media_snapshot"] = {key: copy.deepcopy(value) for key, value in record.items()
                                    if key not in {"media_snapshot", "observations"}}
    return record["media_snapshot"]


def _take_media(cfg, record):
    snapshot = record.get("media_snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("media_job"):
        return False
    status, fields = mediaqueue.import_result(cfg, snapshot)
    if status == "done":
        for key, value in fields.items():
            if value is not None:
                record[key] = value
                snapshot[key] = value
        snapshot.pop("media_job", None)
        record.pop("clip_note", None)
        record["clip_status_text"] = ""
        return True
    if status == "failed" and fields.get("clip_note"):
        record["clip_note"] = fields["clip_note"]
    elif status == "gone":
        snapshot.pop("media_job", None)
    return False


def _share_media(record, incidents):
    """Keep the initial source-grid evidence on promotion until newer media exists."""
    incident = next((item for item in incidents if item.get("id") == record.get("promoted_to")), None)
    if (record.get("promotion_ambiguous") or incident is None or incident.get("clip")
            or not record.get("clip") or not record.get("snap")):
        return
    for key in ("clip", "snap", "clip_updated_at", "clip_style"):
        if record.get(key) is not None:
            incident[key] = record[key]
    incident["early_clip"] = True
    incident["early_clip_time"] = record["first"]


def prepare(cfg, S, now, request):
    """Consume finished early media and start pending work, without posting.

    ``request`` has the mediaqueue.request_clip(cfg, snapshot, now) signature.
    """
    if not enabled(cfg) or not cfg.get("clip", {}).get("enabled", True):
        return 0
    queued = 0
    for record in _store(S.state).values():
        _take_media(cfg, record)
        _share_media(record, S.incidents)
        if record.get("clip") or now - parse_time(record["first"]) > _lifetime(cfg):
            continue
        if record.get("promoted_to") and not record.get("sent_at"):
            continue
        snapshot = _snapshot(record)
        if snapshot.get("media_job"):
            continue
        request(cfg, snapshot, now)
        queued += 1
    return queued


def _media(cfg, record):
    name = record.get("clip")
    if not isinstance(name, str):
        return None
    root = Path(cfg["_site_dir"]).resolve()
    relative = Path(name)
    if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "clips":
        return None
    path = root / relative
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() and not path.is_symlink() else None


def _message(cfg, record, video=False):
    title = "فيديو الرصد الحراري الأولي" if video else "رصد حراري أولي — يحتاج تأكيداً"
    lines = [f"{'🎞' if video else '🛰'} <b>{title}</b>", f"<b>{escape(record['headline'])}</b>",
             f"📍 {escape(record['where'])}",
             "🕒 وقت الرصد: " + local_text(parse_time(record["first"]), cfg, with_date=True),
             "🛰 القمر الأوروبي ميتيوسات من الجيل الثالث (MTG)",
             ("هذا المقطع يعرض الرصد الحراري الأولي في الوقت المذكور." if video else
              "إشارة حرارية أولية؛ لم يؤكدها رصد إضافي بعد."),
             f"<code>{record['lat']:.4f}, {record['lon']:.4f}</code>"]
    if not video and cfg.get("clip", {}).get("enabled", True):
        lines.append("🎞 إذا لم يُرفق الفيديو، فهو قيد التجهيز وسيُرسل هنا عند توفر الصور الصالحة.")
    if cfg.get("app_url"):
        lines.append(f'<a href="{escape(app_link(cfg, record), quote=True)}">فتح الرصد في التطبيق</a>')
    return "\n".join(lines)


def deliver(cfg, S, now, post, prepare_clip):
    """Post first evidence promptly, then deliver its first video once as a reply.

    ``post`` accepts (message, media=None, reply_to=None); ``prepare_clip`` accepts
    an immutable snapshot. It can queue asynchronously, or return ready media.
    """
    if not enabled(cfg):
        return 0
    settings = _settings(cfg)
    max_age = dt.timedelta(minutes=_number(settings.get("max_age_min"), 45))
    limit = int(_number(settings.get("max_new_per_run"), 3))
    attempts = sent_count = 0
    for record in sorted(_store(S.state).values(), key=lambda row: (row["last"], row["id"]), reverse=True):
        if attempts >= limit:
            break
        first = parse_time(record["first"])
        sent_before = bool(record.get("sent_at"))
        if now < first or (not sent_before and (record.get("promoted_to") or now - first > max_age)):
            continue
        if sent_before and (not record.get("video_pending") or now - first > _lifetime(cfg)):
            continue
        if cfg.get("clip", {}).get("enabled", True):
            _take_media(cfg, record)
            if not record.get("clip"):
                snapshot = _snapshot(record)
                ready = prepare_clip(snapshot)
                if ready:
                    for key in ("clip", "snap", "clip_note", "clip_updated_at"):
                        if snapshot.get(key) is not None:
                            record[key] = snapshot[key]
            _share_media(record, S.incidents)
        media = _media(cfg, record)
        if sent_before and media is None:
            continue
        attempts += 1
        sent = post(_message(cfg, record, video=sent_before), media,
                    reply_to=record.get("tg_msg") if sent_before else None)
        if not sent:
            continue
        S.sent = True
        sent_count += 1
        if not sent_before:
            record["sent_at"] = iso(now)
            if sent is not True:
                record["tg_msg"] = sent
        record["video_pending"] = bool(cfg.get("clip", {}).get("enabled", True) and media is None)
        if media is not None:
            record["video_sent_at"] = iso(now)
            if record.get("promoted_to"):
                for incident in S.incidents:
                    if incident.get("id") == record["promoted_to"]:
                        incident["early_video_sent_at"] = iso(now)
        # A clip may finish in the same cycle as genuine evidence is promoted.
        # Its caption still identifies it as the INITIAL observation, not a new
        # measurement or a fire-power update.
    return sent_count


def items(cfg, state, incidents, now):
    """Public persistent previews. Promoted IDs are aliases on the real incident."""
    if not enabled(cfg):
        return preliminary.public_candidates(cfg, state, incidents, now)
    records = state.get("early_night") or {}
    settings = _settings(cfg)
    keep = dt.timedelta(days=_number(settings.get("keep_days"), 7))
    result = []
    for record in records.values():
        last = _time(record.get("last"))
        if record.get("promoted_to") or last is None or now < last or now - last > keep:
            continue
        if _incident_for(record, incidents, settings) is not None:
            continue
        item = {key: copy.deepcopy(record.get(key)) for key in _PUBLIC}
        item["max_frp"], item["frp_series"], item["size"] = None, None, None
        if now - last > _lifetime(cfg):
            item["headline"] = "رصد حراري سابق لم يتأكد" + (
                " قرب " + item["region_name"] if item.get("region_name") else "")
            item["reason"] = "لم يصل تأكيد إضافي لهذا الرصد الحراري"
            item["clip_status_text"] = "" if item.get("clip") else "لم يتأكد الرصد؛ لا يتوفر فيديو حالياً"
        elif item.get("clip"):
            item["clip_status_text"] = ""
        elif not cfg.get("clip", {}).get("enabled", True):
            item["clip_status_text"] = "إنشاء الفيديو غير مفعّل حالياً"
        else:
            item["clip_status_text"] = item.get("clip_note") or "يجري تجهيز فيديو الرصد الحراري الأولي"
        result.append(item)
    represented = {_identity(row) for record in records.values()
                   for row in record.get("observations") or []}
    # Publishing can run without sync (including an older delayed candidate that
    # is deliberately too old for a first Telegram notification). Retain that
    # quiet app-only review mode without making duplicates of persistent views.
    for item in preliminary.public_candidates(cfg, state, incidents, now):
        if _identity(_origin(item)) not in represented:
            result.append(item)
    return sorted(result, key=lambda row: (row["last"], row["id"]), reverse=True)

"""Render clips outside the monitoring cycle, then import only matching results.

The worker owns staging files only. The scanner remains the sole writer of incident
state and the published site, so a slow or obsolete render cannot overwrite new data.
"""
from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace

from .updates import peak_reading
from .util import iso, load_json, log, make_session, parse_time, save_json, utcnow
from .wms import WMS

_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_INPUT_FIELDS = ("id", "lat", "lon", "headline", "where", "country", "place", "region_name",
                 "facility", "verdict", "first", "last", "max_frp", "frp_series", "polar", "srcs")
_RESULT_FIELDS = ("clip", "snap", "clip_max_frp", "clip_peak_time", "clip_style", "clip_updated_at",
                  "clip_note", "unseen_close")


def queue_dir(cfg):
    return Path(cfg["_state_dir"]).resolve().parent / ".rasad-media"


def _latest_path(cfg, incident_id):
    key = hashlib.sha256(str(incident_id).encode()).hexdigest()
    return queue_dir(cfg) / ("latest-" + key + ".json")


def _revision(cfg, inc, refresh):
    from .engine import CLIP_STYLE
    # Presentation settings also belong to a revision; secrets and local paths do not.
    value = {"incident": {k: inc.get(k) for k in _INPUT_FIELDS}, "style": CLIP_STYLE,
             "refresh": bool(refresh), "clip": cfg["clip"],
             "layers": {"eumetview": cfg["eumetview"].get("clip_layer"),
                        "layer": cfg["seawatch"].get("layer"),
                        "cadence": cfg["seawatch"].get("cadence_min")},
             "watermark": cfg.get("watermark"), "logo": cfg.get("watermark_logo"),
             "local_time": cfg.get("local_time")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def _safe_asset(root, name):
    if not isinstance(name, str):
        return None
    part = Path(name)
    if part.is_absolute() or len(part.parts) != 2 or part.parts[0] != "clips" or ".." in part.parts:
        return None
    path = root / part
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return path if not path.is_symlink() and path.is_file() else None


def _local_ready(cfg, inc, refresh):
    site = Path(cfg["_site_dir"])
    if _safe_asset(site, inc.get("clip")) is None or _safe_asset(site, inc.get("snap")) is None:
        return False
    if not refresh:
        return True
    from .engine import CLIP_STYLE
    peak = peak_reading(inc)
    try:
        covered = float(inc.get("clip_max_frp"))
        required = float(inc.get("max_frp") or (peak[1] if peak else 0))
        return (inc.get("clip_style") == CLIP_STYLE
                and inc.get("clip_peak_time") == (peak[0] if peak else None)
                and math.isfinite(covered) and covered >= required - 0.05)
    except (TypeError, ValueError, OverflowError):
        return False


def _retry_due(cfg, job, result, now):
    if not result:
        return True
    if result.get("ok"):
        return False
    retry = float(cfg["clip"].get("refresh_retry_min", 10)) if job.get("refresh") else \
        float(cfg["clip"].get("initial_retry_min", 10))
    # A fire that does not show in the pictures (small, or seen by NASA only) rarely shows later:
    # wait twice as long after each failed try (10, 20, 40 ... minutes, at most 3 hours), so the
    # worker's time goes to fires that can be shown instead of the same ten pictures every 10 min.
    attempts = max(1, int(result.get("attempts") or 1))
    retry = min(retry * 2 ** (attempts - 1), float(cfg["clip"].get("max_retry_min", 180)))
    try:
        return now - parse_time(result["finished_at"]) >= dt.timedelta(minutes=retry)
    except (TypeError, ValueError, KeyError):
        return True


def request_clip(cfg, inc, now, refresh=False):
    """Return True for ready local media, otherwise enqueue without doing network work."""
    if not cfg["clip"].get("enabled", True):
        return False
    refresh = bool(refresh or inc.get("clip_needs_refresh"))
    if _local_ready(cfg, inc, refresh):
        inc.pop("clip_needs_refresh", None)
        return True
    token = _revision(cfg, inc, refresh)
    folder = queue_dir(cfg) / token
    folder.mkdir(parents=True, exist_ok=True)
    inc["media_job"] = token
    save_json(_latest_path(cfg, inc["id"]), {"token": token}, compact=True)
    job = load_json(folder / "job.json", {})
    if not job:
        save_json(folder / "job.json", {"token": token, "created_at": iso(now),
                                       "refresh": refresh, "incident": copy.deepcopy(inc)}, compact=True)
    # Render attempts, rather than enqueue operations, control the retry interval.
    return False


def _lock(folder):
    try:
        handle = open(folder / "render.lock", "a")
    except (FileNotFoundError, NotADirectoryError):
        return None  # the scanner may just have consumed and removed this job
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _valid_job(cfg, folder):
    job = load_json(folder / "job.json", {})
    if (not _TOKEN.fullmatch(folder.name) or job.get("token") != folder.name
            or not isinstance(job.get("incident"), dict)):
        return None
    if _revision(cfg, job["incident"], job.get("refresh")) != folder.name:
        return None
    return job


def process_one(cfg, now=None):
    """Attempt at most one queued render. No Telegram calls or live-state writes."""
    from .engine import add_clip
    fixed_now = now
    now = now or utcnow()
    root = queue_dir(cfg)
    if not root.exists():
        return False
    folders = []
    for path in root.iterdir():
        try:
            if path.is_dir() and not path.is_symlink() and _TOKEN.fullmatch(path.name):
                folders.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            pass  # a completed job was consumed between listing and inspecting it
    folders = [path for _, path in sorted(folders, reverse=True)]
    for folder in folders:
        handle = _lock(folder)
        if handle is None:
            continue
        try:
            job = _valid_job(cfg, folder)
            if not job:
                # A request creates its folder immediately before atomically writing
                # job.json. Give that short hand-off a grace period; changed settings
                # or malformed old jobs must not retain their staging files forever.
                raw = load_json(folder / "job.json", {})
                if raw or now.timestamp() - folder.stat().st_mtime > 60:
                    shutil.rmtree(folder)
                continue
            try:
                expired = now - parse_time(job["created_at"]) > dt.timedelta(days=7)
            except (ValueError, TypeError, KeyError):
                expired = True
            if expired:
                shutil.rmtree(folder)
                continue
            latest = load_json(_latest_path(cfg, job["incident"].get("id")), {})
            if latest.get("token") != folder.name:
                shutil.rmtree(folder)
                continue  # a newer requested revision superseded this queued snapshot
            result = load_json(folder / "result.json", {})
            if not _retry_due(cfg, job, result, now):
                continue
            work = folder / "site"
            work.mkdir(exist_ok=True)
            inc = copy.deepcopy(job["incident"])
            for key in ("clip", "snap", "clip_tried", "clip_refresh_tried", "media_job"):
                inc.pop(key, None)
            worker_cfg = dict(cfg, _site_dir=work, _background_media=False)
            try:
                with make_session() as session:
                    wms = WMS(cfg["eumetview"]["wms_url"], session)
                    ok = add_clip(worker_cfg, wms, SimpleNamespace(state={}), inc, now,
                                  refresh=bool(job.get("refresh")))
                ok = bool(ok and _safe_asset(work, inc.get("clip"))
                          and _safe_asset(work, inc.get("snap")))
            except Exception as e:  # noqa: BLE001
                ok = False
                log.warning("background media failed: %s", e)
            finished = fixed_now or utcnow()
            attempts = 0 if ok else int(result.get("attempts") or 0) + 1
            save_json(folder / "result.json", {"token": folder.name, "ok": ok, "attempts": attempts,
                      "finished_at": iso(finished), "fields": {k: inc.get(k) for k in _RESULT_FIELDS}}, compact=True)
            return True
        finally:
            handle.close()  # flock is released even when the render fails or a process exits
    return False


def _copy_atomic(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def consume_results(cfg, S, now):
    """Import complete matching assets. The scanner alone commits incident state."""
    from .brain import finalize
    imported = 0
    for inc in S.incidents:
        token = inc.get("media_job")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            continue
        folder = queue_dir(cfg) / token
        handle = _lock(folder)
        if handle is None:
            continue  # a worker is still rendering or publishing its result
        try:
            job = _valid_job(cfg, folder)
            if not job or _revision(cfg, inc, job.get("refresh")) != token:
                inc.pop("media_job", None)
                shutil.rmtree(folder)
                continue  # new observations or presentation changes made this render obsolete
            result = load_json(folder / "result.json", {})
            if result.get("token") != token or inc.get("media_result_at") == result.get("finished_at"):
                continue
            fields = result.get("fields")
            if not isinstance(fields, dict):
                continue
            if result.get("ok"):
                sources = [_safe_asset(folder / "site", fields.get(k)) for k in ("clip", "snap")]
                if any(path is None for path in sources):
                    continue
                site = Path(cfg["_site_dir"])
                for key, source in zip(("clip", "snap"), sources):
                    destination = site / fields[key]
                    try:
                        destination.resolve().relative_to(site.resolve())
                    except ValueError:
                        raise ValueError("unsafe media destination") from None
                    _copy_atomic(source, destination)
                for key in _RESULT_FIELDS:
                    if fields.get(key) is None:
                        inc.pop(key, None)
                    else:
                        inc[key] = fields[key]
                inc.pop("clip_needs_refresh", None)
                inc.pop("media_job", None)
                inc.pop("media_result_at", None)
                finalize(inc, cfg)
                imported += 1
                shutil.rmtree(folder)
            else:
                # A failed refresh preserves usable old assets; first-attempt notes still
                # explain why the candidate could not be seen in close pictures.
                key = "clip_refresh_tried" if job.get("refresh") else "clip_tried"
                inc[key] = result["finished_at"]
                inc["media_result_at"] = result["finished_at"]
                if not inc.get("clip"):
                    for key in ("clip_note", "unseen_close"):
                        if fields.get(key) is not None:
                            inc[key] = fields[key]
                    finalize(inc, cfg)
        finally:
            handle.close()
    return imported

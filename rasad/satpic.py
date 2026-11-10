"""The before/after satellite picture sent under a fire's alert («صورة قبل وبعد»).

After an alert the monitor waits for the first clear Sentinel-2 pass over the place (one comes
every two to five days), compares it with the newest clear pass before the fire, and posts one
picture as a reply to the alert: before, after and what changed, with the changed area.

Like the clips, the work is split in two. The scanner (``engine.run_cycle``) only writes a small
job file and later picks up a finished result: it never waits for the archive. The worker
(``python -m rasad satpic-worker``, started by ``scripts/loop.sh``) does the reading and the
drawing, each job with a deadline. The scanner alone posts and remembers that it did.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import time
from pathlib import Path

from PIL import Image, ImageDraw

from . import s2
from .util import esc, iso, load_json, log, make_session, parse_time, save_json, utcnow

MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول",
          "تشرين الأول", "تشرين الثاني", "كانون الأول"]
STYLE = 1          # bump when the picture's look or the comparison's rules change
QUIET_HA, QUIET_SHARE = 0.3, 0.0008     # below both: "no clear change"


def queue_dir(cfg):
    return Path(cfg["_state_dir"]).resolve().parent / ".rasad-satpic"


def _key(inc_id):
    return hashlib.sha256(str(inc_id).encode()).hexdigest()[:40]


def _token(inc):
    """A result belongs to one state of the fire: a later sighting asks for a later picture."""
    return hashlib.sha256(f"{inc['id']}|{inc['first']}|{inc['last']}|{STYLE}".encode()).hexdigest()[:32]


# ---------------------------------------------------------------- the scanner's side
def wanted(inc, cfg, now):
    """Fires that were posted to the channel get one picture, while a pass can still come."""
    S = cfg["satpic"]
    if not S.get("enabled", True) or inc.get("status") != "alerted" or inc.get("recovered_historical"):
        return False
    if (inc.get("satpic") or {}).get("sent_at") or (inc.get("satpic") or {}).get("closed"):
        return False
    if inc.get("place") == "sea" and not S.get("sea", False):
        return False                       # a ship moves away: nothing to compare at that spot
    # switched on in a running monitor: only fires alerted shortly before that, not the whole week
    if S.get("_since") and str(inc.get("alerted_at") or "") < S["_since"]:
        return False
    return now - parse_time(inc["last"]) <= dt.timedelta(days=float(S.get("max_days", 12)))


def request(cfg, inc, now):
    """Queue the picture for this fire (no network: a small file for the worker)."""
    S = cfg["satpic"]
    path = queue_dir(cfg) / "jobs" / (_key(inc["id"]) + ".json")
    token = _token(inc)
    if load_json(path, {}).get("token") == token:
        return
    approximate = inc.get("best") != "polar" and not inc.get("polar")
    save_json(path, {"token": token, "id": inc["id"], "lat": inc["lat"], "lon": inc["lon"], "first": inc["first"],
                     "last": inc["last"], "headline": inc.get("headline") or "", "where": inc.get("where") or "",
                     "half_km": float(S.get("approx_half_km", 3) if approximate else S.get("half_km", 2)),
                     "created_at": iso(now)}, compact=True)


def collect(cfg, inc, now):
    """A finished picture for this fire, if the worker has one that still fits: (path, facts).
    Also closes the request quietly once no pass can come any more."""
    folder = queue_dir(cfg)
    res = load_json(folder / "results" / (_key(inc["id"]) + ".json"), {})
    if not res or res.get("token") != _token(inc):
        return None
    if res.get("ok"):
        picture = folder / "results" / str(res.get("picture") or "")
        if picture.is_file() and picture.parent == folder / "results":
            return picture, res
        return None
    if res.get("final"):
        inc["satpic"] = {"closed": iso(now), "why": res.get("why") or ""}
    return None


def day_ar(t, cfg, with_year=False):
    loc = t + dt.timedelta(hours=float(cfg["local_time"]["offset_hours"]))
    return f"{loc.day} {MONTHS[loc.month - 1]}" + (f" {loc.year}" if with_year else "")


def area_text(ha):
    if ha < 1:
        return f"{max(100, round(ha * 100) * 100):,} م²"
    return f"{ha:.1f} هكتار" if ha < 100 else f"{ha / 100:.2f} كم²"


def is_quiet(res):
    return res.get("area_ha", 0) < QUIET_HA and res.get("share", 0) < QUIET_SHARE


def caption(inc, res, cfg):
    """The reply under the alert. Says what is seen, never a cause; «مؤكد» is not used here."""
    after, before = parse_time(res["after_time"]), parse_time(res["before_time"]) if res.get("before_time") else None
    clock = (after + dt.timedelta(hours=float(cfg["local_time"]["offset_hours"]))).strftime("%H:%M")
    lines = ["🛰 <b>صور الأقمار لموقع الحريق: قبل وبعد</b>"]
    lines.append((f"قبل: {day_ar(before, cfg)} · " if before else "")
                 + f"بعد: {day_ar(after, cfg)} الساعة {clock} ({esc(cfg['local_time']['name'])})")
    if before is None:
        lines.append("لا صورة صافية قبل الحريق للمقارنة: هذه أول صورة صافية بعده.")
    elif is_quiet(res):
        lines.append("لا تغيّر واضح في الموقع بهذه الدقة (الحرائق الصغيرة لا تترك أثراً يُرى من هذا القمر).")
    else:
        lines.append(f"تغيّر مرصود: نحو {area_text(res['area_ha'])} (بالأحمر ما صار أغمق، وبالأزرق ما صار أفتح).")
    if before is not None and (res.get("clouds") or res.get("compared", 1) < 0.9):
        lines.append("غيوم في إحدى الصورتين: التقدير أقل ثقة.")
    if res.get("hot_after"):
        lines.append("🔥 لهب أو حرارة شديدة ظاهرة لحظة التقاط صورة «بعد»"
                     + (" (وكانت ظاهرة قبل الحريق أيضاً: شعلة غاز على الأرجح)." if res.get("hot_before") else "."))
    lines.append("القمر الأوروبي ⁨Sentinel-2⁩‏، كل نقطة 10 أمتار: يظهر الضرر الكبير فقط، والصورة لا تحدد السبب.")
    if cfg.get("app_url"):
        base = cfg["app_url"].split("#")[0].split("?")[0]
        lines.append(f'📱 <a href="{base}?e=ba:i:{inc["id"]}#ba:i:{inc["id"]}">افتح المقارنة في التطبيق</a>')
    return "\n".join(lines)


def facts(inc, res):
    """What the incident keeps (and the app reads) once the picture is sent."""
    return {"before": (res.get("before_time") or "")[:10] or None, "after": res["after_time"][:10],
            "area_ha": round(float(res.get("area_ha", 0)), 2), "quiet": is_quiet(res)}


# ---------------------------------------------------------------- the worker's side
def _due(cfg, job, res, now):
    if not res or res.get("token") != job.get("token"):
        return True
    if res.get("ok") or res.get("final"):
        return False
    wait = dt.timedelta(hours=float(cfg["satpic"].get("retry_hours", 3)))
    try:
        return now - parse_time(res["tried_at"]) >= wait
    except (KeyError, TypeError, ValueError):
        return True


def work_one(cfg, now=None, session=None):
    """Do the oldest job that is due. Returns its id, or None when nothing is due."""
    now = now or utcnow()
    folder = queue_dir(cfg)
    (folder / "results").mkdir(parents=True, exist_ok=True)
    jobs = []
    for path in sorted((folder / "jobs").glob("*.json")):
        job = load_json(path, {})
        if job.get("token") and job.get("id"):
            res = load_json(folder / "results" / path.name, {})
            if _due(cfg, job, res, now):
                jobs.append((res.get("tried_at") or "", job.get("created_at") or "", path.name, job))
    if not jobs:
        return None
    _, _, name, job = min(jobs)
    res = {"token": job["token"], "id": job["id"], "tried_at": iso(now), "ok": False}
    try:
        res.update(_make(cfg, job, now, session, folder / "results" / (name[:-5] + ".jpg")))
    except s2.ArchiveError as e:
        res["why"] = "archive: " + e.code           # tried again after retry_hours
        log.info("satellite picture for %s: the archive did not answer (%s)", job["id"], e.code)
    except Exception as e:  # noqa: BLE001
        res["why"] = type(e).__name__
        log.warning("satellite picture for %s failed: %s", job["id"], e)
    save_json(folder / "results" / name, res, compact=True)
    return job["id"]


def _make(cfg, job, now, session, out):
    S = cfg["satpic"]
    first, last = parse_time(job["first"]), parse_time(job["last"])
    archive = s2.Archive(session or make_session(), deadline=time.monotonic() + float(S.get("job_budget_s", 170)))
    grid = s2.Grid(job["lat"], job["lon"], job["half_km"])
    passes = archive.list_passes(grid, first - s2.days(float(S.get("lookback_days", 75))), now)
    later = [p for p in reversed(passes) if p["time"] > last]          # oldest first
    after = None
    for p in later:
        archive.assess(p, grid)
        if s2.is_clear(p):
            after = p
            break
    if after is None:
        gone = now - last > dt.timedelta(days=float(S.get("max_days", 12)))
        return {"final": gone, "why": "no clear pass after the fire" + (" in time" if gone else " yet")}
    before = None
    for p in (p for p in passes if p["time"] < first):                   # newest first
        archive.assess(p, grid)
        if s2.is_clear(p):
            before = p
            break
    A = archive.load_scene(after, grid)
    B = archive.load_scene(before, grid) if before else None
    change = s2.change_of(B, A, grid.res) if B else None
    res = {"ok": True, "picture": out.name, "after_time": iso(after["time"]),
           "before_time": iso(before["time"]) if before else None, "hot_after": s2.hot_count(A),
           "hot_before": s2.hot_count(B) if B else 0, "megabytes": round(archive.bytes / 1e6, 1)}
    if change:
        res.update({k: float(change[k]) for k in ("area_ha", "dark_ha", "bright_ha", "share", "compared")},
                   clouds=bool(change["clouds"]))
    render(cfg, job, grid, B, A, change, res, out)
    log.info("satellite picture for %s: %s -> %s, %.1f MB read", job["id"], res["before_time"], res["after_time"], res["megabytes"])
    return res


def run_worker(cfg, interval=60, max_minutes=340, once=False):
    session, end = make_session(), time.monotonic() + max_minutes * 60
    while time.monotonic() < end:
        try:
            done = work_one(cfg, session=session)
        except Exception as e:  # noqa: BLE001
            log.warning("satellite picture worker: %s", e)
            done = None
        if once:
            return
        if not done:
            time.sleep(max(1.0, float(interval)))


# ---------------------------------------------------------------- the picture
def _panel(im, rgba, x, y, size):
    tile = Image.fromarray(rgba, "RGBA").resize((size, size), Image.Resampling.BICUBIC)
    im.paste(tile, (x, y), tile)


def render(cfg, job, grid, before, after, change, res, out):
    """A portrait picture (1080 x 1350): before and after side by side, then what changed."""
    from .twopanel import BG, CYAN, DIM, WHITE, _font, _text, _width
    W, H, M, Y = 1080, 1350, 12, 184
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    def fit(text, size, limit, kind="ar"):
        while size > 22 and _width(d, text, _font(size, kind), rtl=True) > limit:
            size -= 2
        f = _font(size, kind)
        while len(text) > 8 and _width(d, text, f, rtl=True) > limit:
            text = text[:-2] + "…"
        return text, f

    _text(d, (W - 28, 26), "قبل وبعد · صور القمر الأوروبي Sentinel-2", _font(26), CYAN, anchor="ra", rtl=True)
    title, f = fit(job.get("headline") or "موقع الحريق", 44, W - 56)
    _text(d, (W - 28, 66), title, f, WHITE, anchor="ra", rtl=True)
    width_km = job["half_km"] * 2
    coords, mono = f"{job['lat']:.4f}, {job['lon']:.4f}", _font(24, "mono")
    _text(d, (28, 136), coords, mono, DIM, anchor="la")        # left-to-right on its own: the numbers keep their order
    sub, f = fit(f"مربع {width_km:g}×{width_km:g} كم" + (f" · {job['where']}" if job.get("where") else ""), 25,
                 W - 76 - _width(d, coords, mono))
    _text(d, (W - 28, 134), sub, f, DIM, anchor="ra", rtl=True)

    both = [s for s in (before, after) if s is not None]
    stretch = s2.stretch_of(both, s2.COLOR_BANDS)
    label_font, small = _font(27), _font(20)

    def panel(scene, x, y, size, label, accent, layer=None):
        _panel(im, s2.composite(scene, s2.COLOR_BANDS, stretch), x, y, size)
        if layer is not None:
            shade = Image.new("RGBA", (size, size), (5, 8, 13, 76))
            im.paste(shade, (x, y), shade)
            tile = Image.fromarray(layer, "RGBA").resize((size, size), Image.Resampling.NEAREST)
            im.paste(tile, (x, y), tile)
        d.rectangle([x, y, x + size - 1, y + size - 1], outline=(46, 52, 62), width=2)
        w = _width(d, label, label_font, rtl=True) + 28
        d.rectangle([x + size - w - 10, y + 10, x + size - 10, y + 56], fill=(5, 8, 13))
        d.rectangle([x + size - 14, y + 10, x + size - 10, y + 56], fill=accent)
        _text(d, (x + size - 26, y + 16), label, label_font, WHITE, anchor="ra", rtl=True)
        metres = next((m for m in (2000, 1000, 500, 200, 100, 50) if m / (width_km * 1000 / size) <= size * 0.3), 50)
        bar = metres / (width_km * 1000 / size)
        d.rectangle([x + 12, y + size - 46, x + 28 + bar, y + size - 10], fill=(5, 8, 13))
        d.rectangle([x + 20, y + size - 20, x + 20 + bar, y + size - 16], fill=WHITE)
        _text(d, (x + 20 + bar / 2, y + size - 44), f"{metres // 1000} كم" if metres >= 1000 else f"{metres} م",
              small, WHITE, anchor="ma", rtl=True)

    after_label = "بعد · " + day_ar(parse_time(res["after_time"]), cfg, True)
    if before is not None:
        P = (W - 3 * M) // 2
        panel(before, W - M - P, Y, P, "قبل · " + day_ar(parse_time(res["before_time"]), cfg, True), WHITE)
        panel(after, M, Y, P, after_label, (255, 122, 26))
        Y2 = Y + P + M
        panel(after, M, Y2, P, "ما تغيّر", CYAN, s2.change_layer(change))
        tx, ty = W - 28, Y2 + 16
        _text(d, (tx, ty), "تغيّر مرصود", _font(26), CYAN, anchor="ra", rtl=True)
        ty += 50
        quiet = is_quiet(res)
        _text(d, (tx, ty), "لا تغيّر واضح" if quiet else "نحو " + area_text(res["area_ha"]), _font(48), WHITE, anchor="ra", rtl=True)
        ty += 78
        if not quiet:
            for colour, text in (((255, 59, 48), "صار أغمق: " + area_text(res["dark_ha"])),
                                 ((34, 211, 238), "صار أفتح: " + area_text(res["bright_ha"]))):
                d.rectangle([tx - 22, ty + 6, tx, ty + 28], fill=colour)
                _text(d, (tx - 34, ty), text, _font(26), WHITE, anchor="ra", rtl=True)
                ty += 42
        notes = []
        if res.get("clouds") or res.get("compared", 1) < 0.9:
            notes.append("غيوم في إحدى الصورتين: التقدير أقل ثقة.")
        if res.get("hot_after"):
            notes.append("لهب ظاهر في صورة «بعد».")
        notes += ["تُظهر الصور التغيّر ولا تحدد سببه.", "كل نقطة 10 أمتار على الأرض."]
        ty += 14
        for note in notes:
            text, f = fit(note, 24, P - 30)
            _text(d, (tx, ty), text, f, DIM, anchor="ra", rtl=True)
            ty += 38
    else:
        panel(after, M, Y, W - 2 * M, after_label, (255, 122, 26))
    brand = str(cfg["clip"].get("hud_title") or "")
    if brand:
        _text(d, (28, H - 40), brand, _font(20, "brand"), (86, 98, 122), anchor="la")
    _text(d, (W - 28, H - 40), "Contains modified Copernicus Sentinel data " + str(utcnow().year), _font(18, "mono"),
          (86, 98, 122), anchor="ra")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=88, optimize=True)
    return out


def preview(cfg, lat, lon, first, last, out, headline="", where="", half_km=2.0, session=None):
    """Make the picture for any place and period without the queue (for checks by hand)."""
    job = {"token": "preview", "id": "preview", "lat": lat, "lon": lon, "first": iso(first), "last": iso(last),
           "headline": headline, "where": where, "half_km": half_km}
    res = _make(cfg, job, utcnow(), session, Path(out))
    return res, (caption({"id": "preview"}, res, cfg) if res.get("ok") else None)


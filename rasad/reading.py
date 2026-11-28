"""«قراءة رصد»: Rasad's own reading of an event once it has run its course, from what it measured.

Written for the owner on 25 Nov 2026 ("make the tool analyse like you"). Its model is the reading of
the Shaybah heat that day: 51 MW, 57 MW, 42 MW in three pictures, gone by the fourth, no smoke in
three daytime pictures, inside the plant, at a spot NASA saw hot on 3 of 197 dates since 2024:
"more like an emergency gas flare than a fire". Every line is a measured fact with its number; the
last says what the facts look like, in the words the channel already uses («حرق غاز» / «حريق»),
and never a cause: never «هجوم», «استهداف» or «ضربة» (tests/test_reading.py scans every reading).

It goes out once as a reply under the event's post, when the event has been quiet for an hour
(`reading.quiet_min`) or has burnt for `reading.long_hours`, while it is still news. The app gets
the same lines (`inc["reading"]`).
"""
from __future__ import annotations

import datetime as dt
from html import escape

from .util import haversine_km, parse_time

FORBIDDEN = ("هجوم", "استهداف", "ضربة", "قصف", "مسيّرة", "صاروخ", "تخريب", "اعتداء")
NOTE = "قراءة آلية من قياسات الأقمار؛ الأقمار ترى الحرارة ولا ترى سببها."
SETTINGS = {"enabled": True, "post": True, "quiet_min": 60, "long_hours": 6, "max_age_hours": 12,
            "min_pictures": 2}


def settings(cfg):
    return dict(SETTINGS, **(cfg.get("reading") or {}))


def _times(inc):
    """Every sighting time, oldest first."""
    ts = set(inc.get("geo_slots") or [])
    ts |= {p["t"] for p in inc.get("polar") or [] if p.get("t")}
    ts |= {o["t"] for o in inc.get("observations") or [] if o.get("t")}
    return sorted(parse_time(t) for t in ts)


def _hm(t, cfg):
    lt = cfg["local_time"]
    return (t + dt.timedelta(hours=float(lt["offset_hours"]))).strftime("%H:%M")


def _span_ar(minutes):
    m = int(round(minutes))
    if m < 15:
        return "أقل من ربع ساعة"
    if m < 50:
        return f"نحو {int(round(m / 10.0) * 10)} دقيقة"
    h = m / 60.0
    if h < 1.25:
        return "نحو ساعة"
    if h < 1.75:
        return "نحو ساعة ونصف"
    if h < 2.5:
        return "نحو ساعتين"
    n = int(round(h))
    return f"نحو {n} ساعات" if n <= 10 else f"نحو {n} ساعة"


def _pictures_ar(n):
    return "صورة واحدة" if n == 1 else "صورتين" if n == 2 else f"{n} صور" if n <= 10 else f"{n} صورة"


def _stretches(ts, gap_h=3.5):
    """The sighting times cut where nothing was seen for `gap_h` hours: [(start, end), ...]."""
    out = []
    for t in ts:
        if out and t - out[-1][1] <= dt.timedelta(hours=gap_h):
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b) for a, b in out]


def _series(inc):
    rows = []
    for r in inc.get("frp_series") or []:
        try:
            rows.append((parse_time(r[0]), float(r[1])))
        except (TypeError, ValueError, IndexError):
            continue
    for p in inc.get("polar") or []:
        if p.get("frp") is not None and p.get("t"):
            rows.append((parse_time(p["t"]), float(p["frp"])))
    best = {}
    for t, v in rows:
        best[t] = max(best.get(t, 0.0), v)
    return sorted(best.items())


def _shape(series):
    """How the strength went: 'sudden' (strong from the first readings, then down), 'growing', 'steady'
    or None (too few readings to say)."""
    if len(series) < 3:
        return None
    vals = [v for _, v in series]
    peak = max(vals)
    k = vals.index(peak)
    t0, tp = series[0][0], series[k][0]
    if vals[0] >= 0.6 * peak and (tp - t0) <= dt.timedelta(minutes=30) and vals[-1] < peak:
        return "sudden"
    if (tp - t0) >= dt.timedelta(minutes=60) and vals[0] <= 0.5 * peak:
        return "growing"
    if peak <= 1.6 * max(min(vals), 0.1) and len(vals) >= 4:
        return "steady"
    return None


def _moved(inc):
    obs = sorted((o for o in inc.get("observations") or [] if o.get("lat") is not None), key=lambda o: o["t"])
    if len(obs) < 2:
        return None
    a, b = obs[0], obs[-1]
    km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    if km < 2.0:
        return None
    from .util import bearing_deg, direction_ar
    return km, direction_ar(bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"]))


def facts(inc, cfg, now, atlas=None):
    """The measured facts of the event, or None while it is still too thin or still burning."""
    P = settings(cfg)
    ts = _times(inc)
    pictures = len(set(inc.get("geo_slots") or []))
    passes = {(p.get("src"), str(p.get("t", ""))[:15]) for p in inc.get("polar") or []}
    if not ts or (pictures < int(P["min_pictures"]) and len(passes) < 2):
        return None
    first, last = ts[0], ts[-1]
    stretches = _stretches(ts)
    quiet = now - last >= dt.timedelta(minutes=float(P["quiet_min"]))
    long_ = last - first >= dt.timedelta(hours=float(P["long_hours"]))
    if not (quiet or long_):
        return None
    series = _series(inc)
    f = inc.get("facility") or {}
    at_site = inc.get("place") == "land" and (inc.get("kind") == "unusual" or (f.get("km") is not None and f["km"] <= 3.0))
    from .util import sun_elevation
    dawn = inc.get("place") in ("sea", "coast") and \
        sun_elevation(inc["lat"], inc["lon"], last + dt.timedelta(minutes=30)) > -6
    out = {"first": first, "last": last, "stretches": stretches, "dawn": dawn,
           "minutes": sum((b - a).total_seconds() for a, b in stretches) / 60.0 + 10.0 * len(stretches),
           "quiet_days": inc.get("quiet_days"), "new_place": (inc.get("known_days") or 0) == 0 and (inc.get("area_days") or 0) == 0,
           "ended": quiet, "pictures": pictures, "passes": len(passes), "series": series, "shape": _shape(series),
           "peak": max((v for _, v in series), default=None), "smoke": inc.get("smoke") or {},
           "at_site": at_site, "place": inc.get("place"), "moved": _moved(inc) if inc.get("place") in ("sea", "coast") else None,
           "near": inc.get("near_confirmed"), "linked": inc.get("linked"), "wide": inc.get("wide"),
           "warn": [w.get("key") for w in ((inc.get("signals") or {}).get("warn") or [])],
           "typical": inc.get("typical_frp"), "habit": inc.get("site_habit"), "spot_days": None, "dates": None}
    try:
        from . import patterns
        A = patterns.atlas() if atlas is None else atlas
        if A and int(A.get("dates") or 0) >= 50:
            out["spot_days"] = patterns.spot_count(A, inc["lat"], inc["lon"], 0.75)
            out["dates"] = int(A["dates"])
    except Exception:  # noqa: BLE001 - a missing map only leaves the line out
        pass
    return out


def judgement(F):
    """What the measured facts look like, in plain words, or None when they do not lean either way.
    At a plant: an emergency gas flare (short, strong from the start, then down, no smoke) or a fire
    (smoke, or burning on for hours, growing or spread wide). Elsewhere: no judgement of that kind."""
    if not F["at_site"]:
        return None
    smoke_seen = bool(F["smoke"].get("seen"))
    long_burn = F["minutes"] >= 180
    wide = bool(F["wide"]) or "wide" in F["warn"]
    if smoke_seen or wide or (long_burn and F["shape"] in ("growing", "steady", None)):
        why = [w for w, on in (("ظهر دخان", smoke_seen), ("استمر ساعات", long_burn), ("انتشر على مساحة واسعة", wide)) if on]
        return "يشبه حريقاً أكثر مما يشبه حرق غاز (" + "، ".join(why) + ")"
    if F["minutes"] <= 90 and F["shape"] == "sudden" and F["ended"]:
        why = ["قصير", "بدأ قوياً ثم خفّ"]
        if F["smoke"].get("checked") and not smoke_seen:
            why.append("بلا دخان في صور النهار")
        return "يشبه حرقاً طارئاً للغاز في المنشأة أكثر مما يشبه حريقاً (" + "، ".join(why) + ")"
    return "لا تكفي القياسات للترجيح بين حرق غاز وحريق"


def lines(inc, cfg, F):
    """The reading as lines of plain text (the post escapes them; the app shows them as they are)."""
    out = []
    zone = f"بتوقيت {cfg['local_time']['name']}"
    parts = [f"{_hm(a, cfg)} – {_hm(b, cfg)}" if b > a else _hm(a, cfg) for a, b in F["stretches"]]
    if len(parts) == 1:
        span = f"{_span_ar(F['minutes'])} ({parts[0]} {zone})"
    else:
        span = f"{_span_ar(F['minutes'])} على {len(parts) if len(parts) > 2 else 'فترتين'}" \
               f"{' فترات' if len(parts) > 2 else ''} ({' ثم '.join(parts)} {zone})"
    seen = []
    if F["pictures"]:
        seen.append(f"{_pictures_ar(F['pictures'])} للقمر الأوروبي")
    if F["passes"]:
        seen.append({1: "مرور واحد لأقمار ناسا", 2: "مرورين لأقمار ناسا"}.get(F["passes"], f"{F['passes']} مرورات لأقمار ناسا"))
    end = ("، ثم لم تعد تظهر" + (" (بعد الفجر لا تظهر الحرارة الصغيرة في البحر في صور النهار)" if F["dawn"] else "")) \
        if F["ended"] else "، وما زالت مستمرة"
    out.append(f"⏱ المدة: {span}" + (f"، في {' و'.join(seen)}" if seen else "") + end)
    S = F["series"]
    if len(S) == 1:
        out.append(f"📈 الشدة: قيست مرة واحدة: {S[0][1]:.0f} MW")
    elif F["peak"]:
        peak_t = next(t for t, v in S if v == F["peak"])
        after = int(round((peak_t - S[0][0]).total_seconds() / 60.0))
        when = "في أول قراءة" if after <= 0 else f"بعد {_span_ar(after).replace('نحو ', '')} من بدايتها"
        line = f"📈 الشدة: بلغت ذروتها {F['peak']:.0f} MW {when}"
        if len(S) >= 2 and S[-1][1] != F["peak"]:
            line += f"، وآخر قراءة {S[-1][1]:.0f} MW"
        shape = {"sudden": "بدأت قوية ثم خفّت", "growing": "نمت تدريجياً", "steady": "بقيت شبه ثابتة"}.get(F["shape"])
        out.append(line + (f" ({shape})" if shape else ""))
    sm = F["smoke"]
    if sm.get("seen"):
        out.append("💨 الدخان: ظهر دخان من موضع الحرارة في صور النهار")
    elif sm.get("checked"):
        out.append(f"💨 الدخان: لم يظهر دخان في صور النهار ({_pictures_ar(int(sm['checked']))})")
    place = []
    if F["spot_days"] is not None and F["place"] == "land":
        n, d = F["spot_days"], F["dates"]
        if n == 0:
            place.append(f"لم تظهر حرارة في هذه النقطة في سجل ناسا ({d} يوماً منذ 2024)")
        elif n <= 0.02 * d:
            place.append(f"نقطة نادراً ما تظهر فيها حرارة ({n} من {d} يوماً منذ 2024 في سجل ناسا)")
        elif n >= 0.10 * d:
            place.append(f"مكان شعلة معتادة (حرارة في {round(100 * n / d)}% من الأيام منذ 2024)")
    if F["place"] == "land" and not F["at_site"] and F["new_place"]:
        place.append("لم تُرصد حرارة هنا ولا حوله خلال شهر، ولا منشأة مسجّلة في الخريطة")
    if F["at_site"] and F["habit"]:
        place.append(f"أعلى ما تبلغه المنشأة في أيامها النشطة نحو {F['habit']:.0f} MW")
    elif F["at_site"] and F["typical"]:
        place.append(f"حرارتها المعتادة نحو {F['typical']:.0f} MW")
    if F["moved"]:
        km, way = F["moved"]
        place.append(f"تحرّك موضع الحرارة نحو {km:.0f} كم باتجاه {way} خلال {_span_ar(F['minutes']).replace('نحو ', '')}")
    if F["near"]:
        from .brain import hours_ar
        place.append(f"قبله بـ{hours_ar(F['near']['hours'])} رُصد حريق مؤكد على بعد {F['near']['km']:.0f} كم")
    if F["linked"]:
        place.append("ظهرت حرارة جديدة في موقع آخر في الوقت نفسه تقريباً")
    if place:
        out.append("🗺 المكان: " + "؛ ".join(place))
    j = judgement(F)
    if j:
        out.append(f"⚖️ الترجيح: {j}")
    out.append(f"ℹ️ {NOTE}")
    return out


def due(inc, cfg, now, incidents=()):
    """Read once, for a posted event, when it has run its course, while it is still news. A reply under
    an older post (one ship, one thread: `thread_of`) gets no reading of its own, and a thread's first
    post waits while heat answered under it is still seen (its replies are part of its course)."""
    P = settings(cfg)
    if not (P.get("enabled") and P.get("post")) or inc.get("reading_posted") or not inc.get("tg_msg"):
        return False
    if inc.get("status") != "alerted" or inc.get("recovered_historical") or inc.get("thread_of"):
        return False
    quiet = dt.timedelta(minutes=float(P.get("quiet_min", 60)))
    if any(o.get("thread_of") == inc.get("id") and now - parse_time(o["last"]) < quiet for o in incidents or ()):
        return False
    return now - parse_time(inc["last"]) <= dt.timedelta(hours=float(P["max_age_hours"]))


def make(inc, cfg, now, atlas=None):
    """The reading's lines (kept on the incident for the app), or None."""
    if not settings(cfg).get("enabled"):
        return None
    F = facts(inc, cfg, now, atlas)
    if F is None:
        return None
    out = lines(inc, cfg, F)
    text = " ".join(out)
    if any(w in text for w in FORBIDDEN):       # a reading never names a cause; drop it rather than post one
        return None
    return out


def message(out):
    """The reply under the event's post."""
    return "\n".join(["📋 <b>قراءة رصد للحدث</b>"] + [escape(x) for x in out])

"""Heat patterns (أنماط الحرارة): what the heat of an event at or near an energy site looks like, in
words that never name a cause.

Satellites see heat, not why it is there. A pumping station burning after a strike is as hot as a
flare, a refinery accident looks like a strike, and many strikes leave no heat at all (Khor Mor,
8 Nov 2026). So nothing here says «هجوم» or «استهداف»: each label describes the heat only (how
often this spot burns, how far above its own habit, how wide one satellite pass saw it, how long it
lasted, whether other places started at the same time), with the facts behind it.

SILENT for now: the label and its reasons are shown in the app (the neutral '·' line of the
assessment, `inc["pattern"]`); nothing here posts, changes a verdict, the confidence number, the
priority or an alert, until its record over real events has been reviewed with the owner. The design
and its replay on the week of 9-16 Nov 2026: docs/plans/heat_patterns.md.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

from .util import haversine_km, parse_time

LABELS = {
    "solar": "انعكاس محتمل في محطة شمسية",
    "wide": "حرارة واسعة الانتشار",
    "surge": "اشتداد في موقع حرارة معتاد",
    "new_sustained": "حرارة جديدة مستمرة",
    "short": "حرارة قصيرة أو متقطعة",
    "busy": "حرارة في منطقة نشطة",
    "unclear": "حرارة دون نمط واضح",
}
LINKABLE = ("new_sustained", "wide")
LINKED = "عدة مواقع في وقت متقارب"
NO_CAUSE = "صور الأقمار ترى الحرارة ولا ترى سببها"
PREFIX = "النمط: "

PASS_GAP_S = 600       # pixels of one satellite within ten minutes are one pass over the place
MAX_POINTS = 40        # positions kept per pass (enough for the widest pass seen: 27 pixels)
MAX_VALUES = 60
MAX_PASSES = 12
POINTS_KEEP_H = 12     # a pass's positions are needed only while its pixels can still arrive
FOOT_CELL = 0.05       # ~5 km cells for the usual footprint of a place

# NOAA's persistent-anomaly categories (FP_PersistentAnomalyCategory, a 2017 mask; stale for newer
# plants, so it may add evidence, never take an alert away)
PA_OIL_GAS, PA_SOLAR = 1, 3


# ----------------------------------------------------------------- at ingestion
def note_pass(inc, det):
    """Add one new NASA pixel to the summary of its satellite pass over this event: how many 375 m
    pixels, how far apart the two farthest, the distinct strengths (NOAA repeats one 750 m value on
    up to four pixels), and NOAA's tag for the place. `inc["polar"]` keeps only 20 readings and the
    observations 120 shared with Meteosat, so a wide pass would be cut there."""
    if det.get("kind") != "polar":
        return
    try:
        t = parse_time(det["t"])
        lat, lon = float(det["lat"]), float(det["lon"])
    except (KeyError, TypeError, ValueError):
        return
    passes = inc.setdefault("passes", [])
    p = next((p for p in passes if p.get("src") == det.get("src")
              and abs((parse_time(p["t"]) - t).total_seconds()) <= PASS_GAP_S), None)
    if p is None:
        for old in passes:   # older passes are complete: their positions are not needed any more
            if old.get("pts") and t - parse_time(old["t"]) > dt.timedelta(hours=POINTS_KEEP_H):
                old.pop("pts")
        p = {"src": det.get("src"), "t": det["t"], "n": 0, "km": 0.0, "mw": [], "night": bool(det.get("night"))}
        passes.append(p)
    p["n"] += 1
    p["t"] = min(p["t"], det["t"])
    pts = p.setdefault("pts", [])
    if pts:
        p["km"] = round(max(p.get("km") or 0.0, max(haversine_km(lat, lon, a, b) for a, b in pts)), 2)
    if len(pts) < MAX_POINTS:
        pts.append([round(lat, 4), round(lon, 4)])
    frp = det.get("frp")
    if frp is not None:
        value = round(float(frp), 2)   # as NOAA and FIRMS give it: two values are one only if equal
        if value not in p["mw"] and len(p["mw"]) < MAX_VALUES:
            p["mw"].append(value)
    if det.get("pa"):
        p["pa"] = sorted(set(p.get("pa") or []) | {int(det["pa"])})
    if len(passes) > MAX_PASSES:
        widest = max(passes, key=lambda q: (q["n"], q.get("km") or 0.0))
        passes.remove(min((q for q in passes if q is not widest), key=lambda q: q["t"]))
    passes.sort(key=lambda q: q["t"])


def merge_passes(keep, other):
    """Two records of the same fire joined: their passes add up (the same pass seen in both is
    counted by its larger half; positions are not mixed)."""
    passes = keep.setdefault("passes", [])
    for q in other.get("passes") or []:
        p = next((p for p in passes if p.get("src") == q.get("src")
                  and abs((parse_time(p["t"]) - parse_time(q["t"])).total_seconds()) <= PASS_GAP_S), None)
        if p is None:
            passes.append(dict(q))
        elif (q["n"], q.get("km") or 0) > (p["n"], p.get("km") or 0):
            passes[passes.index(p)] = dict(q)
    passes.sort(key=lambda q: q["t"])
    while len(passes) > MAX_PASSES:
        widest = max(passes, key=lambda q: (q["n"], q.get("km") or 0.0))
        passes.remove(min((q for q in passes if q is not widest), key=lambda q: q["t"]))


def foot_key(lat, lon):
    return f"{math.floor(lat / FOOT_CELL)},{math.floor(lon / FOOT_CELL)}"


def record_footprints(state, dets, today, keep_days=30):
    """Every VIIRS pass of this check, routine flares included: per ~5 km cell and day, the most
    pixels one pass showed in that cell. A place's usual footprint is what one pass normally shows
    there (big fields have many flares: West Qurna shows 7-30 pixels and Rumaila 20-50 in one pass
    every day), so a wide pass is measured against the place's own habit, not a single stack."""
    days = state.setdefault("footprint_days", {})
    state.setdefault("footprint_since", today)
    # one pass can come in two checks (NOAA's files of one pass are published minutes apart), so
    # its count per cell adds up over a day before the cell keeps the busiest pass
    passes = state.setdefault("footprint_passes", {})
    touched = set()
    for d in dets:
        if d.get("kind") != "polar" or not str(d.get("src", "")).startswith("VIIRS") or d.get("_history"):
            continue
        try:
            key = f"{d['src']}|{str(d['t'])[:15]}|{foot_key(float(d['lat']), float(d['lon']))}"
        except (KeyError, TypeError, ValueError):
            continue
        passes[key] = passes.get(key, 0) + 1
        touched.add(key)
    for key in touched:
        n = passes[key]
        _, minute, cell = key.split("|")
        row = days.setdefault(cell, {})
        day = minute[:10]
        row[day] = max(row.get(day, 0), n)
    yesterday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    for key in [k for k in passes if k.split("|")[1][:10] < yesterday]:
        del passes[key]
    lo = (dt.date.fromisoformat(today) - dt.timedelta(days=keep_days)).isoformat()
    for key in list(days):
        kept = {d: n for d, n in days[key].items() if d >= lo}
        if kept:
            days[key] = kept
        else:
            del days[key]


def usual_pixels(state, lat, lon, before_day, window_days=30, learn_days=7):
    """The pixels one VIIRS pass usually shows around a place (the 3x3 cells, ~15 km), on the days
    before `before_day` that had heat there: the median of each day's sum. The sum of each cell's
    busiest pass leans high on purpose: a busy field never looks 'wide'. 0 when no earlier pass showed 2+; None
    while fewer than `learn_days` days of passes have been kept (then nothing is called wide)."""
    since = state.get("footprint_since")
    if not since or (dt.date.fromisoformat(before_day) - dt.date.fromisoformat(since)).days < learn_days:
        return None
    days = state.get("footprint_days") or {}
    i, j = math.floor(lat / FOOT_CELL), math.floor(lon / FOOT_CELL)
    lo = (dt.date.fromisoformat(before_day) - dt.timedelta(days=window_days)).isoformat()
    per_day = {}
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for day, n in (days.get(f"{i + di},{j + dj}") or {}).items():
                if lo <= day < before_day:
                    per_day[day] = per_day.get(day, 0) + n
    return float(statistics.median(per_day.values())) if per_day else 0.0


def quiet_days(memory, lat, lon, before_day, window_days, reach=1):
    """Days since this place last had heat, before `before_day` (None: none within the window)."""
    i, j = math.floor(lat / memory_cell()), math.floor(lon / memory_cell())
    lo = (dt.date.fromisoformat(before_day) - dt.timedelta(days=window_days)).isoformat()
    last = None
    for di in range(-reach, reach + 1):
        for dj in range(-reach, reach + 1):
            for day in (memory.cells.get(f"{i + di},{j + dj}") or {}):
                if lo <= day < before_day and (last is None or day > last):
                    last = day
    if last is None:
        return None
    return (dt.date.fromisoformat(before_day) - dt.date.fromisoformat(last)).days


def memory_cell():
    from .brain import CELL
    return CELL


def slot_read(slots, lat, lon, start, end, layer=None):
    """Was a European fire picture covering this point read successfully between start and end
    (of `layer` only, when given)? geocollect keeps a slot only for a picture actually read; keys:
    layer|w,s,e,n|time."""
    for key in slots or {}:
        try:
            name, box, when = key.split("|")
            if layer is not None and name != layer:
                continue
            w, s, e, n = (float(v) for v in box.split(","))
            t = parse_time(when)
        except (ValueError, TypeError):
            continue
        if start <= t <= end and w <= lon <= e and s <= lat <= n:
            return True
    return False


def onset_facts(inc, det, slots):
    """At creation: was Meteosat read at the place around the first sighting? For a daytime NASA
    pixel, a reading within 20 minutes that showed nothing makes sun glint on panels likely; for a
    Meteosat start, a reading 10-40 minutes before shows the start is real and not the end of a gap
    in the readings (two places 'starting together' after a gap is the reading, not the fire)."""
    try:
        t = parse_time(det["t"])
        lat, lon = float(det["lat"]), float(det["lon"])
    except (KeyError, TypeError, ValueError):
        return
    if det.get("kind") == "polar":
        inc["geo_read_at_pass"] = slot_read(slots, lat, lon, t - dt.timedelta(minutes=20),
                                            t + dt.timedelta(minutes=20))
    else:   # the same product: Meteosat-9 read at 21:00 says nothing about a gap in Meteosat-12's
        inc["onset_known"] = slot_read(slots, lat, lon, t - dt.timedelta(minutes=40),
                                       t - dt.timedelta(minutes=10), layer=det.get("src"))


# ----------------------------------------------------------------- what may post (since 16 Nov 2026)
def quiet_enough(inc, days):
    """Quiet for `days` before the first sighting. An event made before its quiet days were kept is
    quiet only if no earlier day had heat at all."""
    if "quiet_days" in inc:   # None: no heat here within the whole window
        return inc["quiet_days"] is None or inc["quiet_days"] >= days
    return not inc.get("known_days")


def new_heat_path(inc, cfg):
    """Which road makes confirmed heat on land in the dry Gulf countries news far from every mapped
    facility (None when none):
    'a'      no heat here nor around for the memory's whole window, 4 pictures (the rule of 15 Nov:
             the pumping station south of Judah);
    'strong' the same quiet place, 20 MW at once: two families already agree (a strong fire is not
             made to wait);
    'b'      quiet for a week, heat on at most two earlier days here, at most one around, and burning
             on: 6 hours and 12 pictures, or 3 hours where NOAA tags the place oil and gas (Abqaiq
             north, 15 Nov: confirmed, 22 hours, 60 pictures, last heat 14 days before, never posted).
    The words of a post on road 'b' say what is known: «حرارة جديدة مستمرة», never a cause."""
    A = cfg["alerts"]
    names = A.get("new_heat_countries") or []
    country = inc.get("country") or ""
    if inc.get("kind") != "new" or inc.get("place") != "land" or inc.get("verdict") != "مؤكد" \
            or not any(n in country for n in names):
        return None
    known, area = inc.get("known_days") or 0, inc.get("area_days") or 0
    frp = inc.get("max_frp") or 0
    pictures = len(inc.get("geo_slots") or [])
    if known == 0 and area == 0 and frp >= float(A.get("new_heat_min_mw", 5)):
        if pictures >= int(A.get("new_heat_min_pictures", 4)):
            return "a"
        if frp >= float(A.get("new_heat_strong_mw", 20)):
            return "strong"
    if not A.get("new_heat_sustained", True):
        return None
    if known <= int(A.get("new_heat_max_known_days", 2)) and area <= int(A.get("new_heat_max_area_days", 1)) \
            and quiet_enough(inc, int(A.get("new_heat_quiet_days", 7))) \
            and frp >= float(A.get("new_heat_sustained_min_mw", 10)) and len(inc.get("families") or []) >= 2:
        hours = (parse_time(inc["last"]) - parse_time(inc["first"])).total_seconds() / 3600.0
        if (hours >= float(A.get("new_heat_sustained_hours", 6))
                and pictures >= int(A.get("new_heat_sustained_pictures", 12))) \
                or (PA_OIL_GAS in _tags(inc) and hours >= float(A.get("new_heat_tagged_hours", 3))):
            return "b"
    return None


def solar_glint(inc, cfg):
    """NASA's daytime pixels on the panels of a mapped solar plant (or a place NOAA tags as panels),
    while a Meteosat picture read at that time showed nothing: sun glint, not heat on the ground. The
    «حريق جديد في الإمارات» post of 11 Nov 2026 (141.6 MW, 15 day pixels 1.4 km from the Dubai solar
    park, Meteosat read at 09:20 and 09:30 UTC) was one. Kept out of the channel since 16 Nov."""
    obs = inc.get("observations") or []
    if inc.get("place") != "land" or not obs or inc.get("geo_slots") or inc.get("geo_read_at_pass") is not True:
        return False
    if not all(o.get("kind") == "polar" and not o.get("night") for o in obs):
        return False
    km = inc.get("solar_km")
    return (km is not None and float(km) <= float(cfg["alerts"].get("solar_glint_km", 3))) or PA_SOLAR in _tags(inc)


# ----------------------------------------------------------------- the label
def footprint(inc):
    """The widest VIIRS pass over this event, or None: pixels, farthest pair (km), the sum of its
    distinct strengths (MW), when."""
    viirs = [p for p in inc.get("passes") or [] if str(p.get("src", "")).startswith("VIIRS")]
    if not viirs:
        return None
    p = max(viirs, key=lambda q: (q["n"], q.get("km") or 0.0, sum(q.get("mw") or [])))
    return {"n": p["n"], "km": round(float(p.get("km") or 0.0), 1), "mw": round(sum(p.get("mw") or []), 1),
            "t": p["t"], "src": p.get("src")}


def second_look(inc, when, hours):
    """Another pass, or two Meteosat pictures, within `hours` of `when` (not the same pass)."""
    t0 = parse_time(when)
    span = dt.timedelta(hours=hours)
    others = [p for p in inc.get("passes") or []
              if dt.timedelta(minutes=15) < abs(parse_time(p["t"]) - t0) <= span]
    if others:
        return True
    pictures = [s for s in inc.get("geo_slots") or [] if abs(parse_time(s) - t0) <= span]
    return len(pictures) >= 2


def _country_ok(inc, cfg):
    names = cfg["alerts"].get("new_heat_countries") or []
    country = inc.get("country") or ""
    return any(n in country for n in names)


def _tags(inc):
    return {pa for p in inc.get("passes") or [] for pa in p.get("pa") or []}


def in_scope(inc, cfg):
    """Heat on land at or near an energy site: a mapped facility within 10 km, a plant surge, a
    place NOAA tags as oil and gas, or anywhere in the dry countries of the Gulf. Sea fires, and
    farm and city fires elsewhere, are left to the existing rules."""
    if inc.get("place") != "land" or inc.get("category") == "candidate":
        return False
    P = cfg.get("patterns") or {}
    fac = inc.get("facility") or {}
    return (inc.get("kind") == "unusual"
            or (fac.get("km") is not None and float(fac["km"]) <= float(P.get("scope_facility_km", 10)))
            or PA_OIL_GAS in _tags(inc) or _country_ok(inc, cfg))


def _hours(inc):
    return (parse_time(inc["last"]) - parse_time(inc["first"])).total_seconds() / 3600.0


def _mw(v):
    return f"{v:.0f} MW" if v >= 10 else f"{v:.1f} MW"


def _n(n, one, two, few, many):
    """Arabic counting: «يوم واحد», «يومين», «3 أيام», «14 يوماً»."""
    n = int(n)
    return one if n == 1 else two if n == 2 else f"{n} {few}" if 3 <= n <= 10 else f"{n} {many}"


def pattern_of(inc, cfg):
    """{key, label, reasons, also} for an event in scope, else None. Checked in this order: sun
    glint on a solar plant, heat spread wide in one pass, a steady site far above its habit, new
    sustained heat, and everything else (short, intermittent or in a busy area)."""
    P = cfg.get("patterns") or {}
    if not P.get("enabled", True) or not in_scope(inc, cfg):
        return None
    tags = _tags(inc)
    pictures = len(inc.get("geo_slots") or [])
    mw = float(inc.get("max_frp") or 0.0)
    known, area, quiet = inc.get("known_days") or 0, inc.get("area_days") or 0, inc.get("quiet_days")
    window = int(cfg["memory"].get("window_days", 30))
    foot = footprint(inc)
    also = []
    if inc.get("linked"):
        L = inc["linked"]
        when = (f"خلال {_n(L['min'], 'دقيقة', 'دقيقتين', 'دقائق', 'دقيقة')}" if L.get("min")
                else "في الوقت نفسه")
        also.append(f"ظهرت حرارة جديدة في موقع آخر على بعد {L['km']:.0f} كم {when}")
    window = int(inc.get("memory_days") or window)   # never claim more days than the memory holds

    # 1) sun on solar panels: daytime NASA pixels only, at a mapped solar plant (or a place NOAA tags
    #    as panels), while a Meteosat picture read at that time showed nothing (not posted since 16 Nov)
    solar_km = inc.get("solar_km")
    if solar_glint(inc, cfg):
        where = (f"على بعد {float(solar_km):.1f} كم من محطة طاقة شمسية مرسومة"
                 if solar_km is not None and float(solar_km) <= float(cfg["alerts"].get("solar_glint_km", 3))
                 else "NOAA تصنّف هذه النقطة ألواحاً شمسية")
        return _out("solar", [where, "رآه قمر ناسا نهاراً فقط",
                              "لم تُظهره صور Meteosat المقروءة في الوقت نفسه",
                              "الأرجح انعكاس الشمس على الألواح"], also)

    # 2) many separate hot points over kilometres in one pass, with a second look within hours: an
    #    area burning, not one flare stack (the footprint of the big plant fires of 2026)
    #    Once seen with its second look it is kept (`inc["wide"]`): the passes and pictures around it
    #    are trimmed on a fire that burns for days, and the label must not fall back then.
    if foot and foot["n"] >= int(P.get("wide_min_px", 8)) and foot["km"] >= float(P.get("wide_min_km", 1.5)) \
            and foot["mw"] >= float(P.get("wide_min_mw", 50)) \
            and inc.get("usual_px") is not None \
            and foot["n"] >= float(P.get("wide_usual_ratio", 3)) * float(inc["usual_px"]) \
            and second_look(inc, foot["t"], float(P.get("second_look_h", 3))) \
            and (foot["n"], foot["km"]) >= ((inc.get("wide") or {}).get("n", 0), (inc.get("wide") or {}).get("km", 0)):
        inc["wide"] = {"n": foot["n"], "km": foot["km"], "mw": foot["mw"], "t": foot["t"],
                       "usual": float(inc["usual_px"])}
    if inc.get("wide"):
        W = inc["wide"]
        points = _n(W["n"], "نقطة ساخنة", "نقطتين ساخنتين", "نقاط ساخنة منفصلة", "نقطة ساخنة منفصلة")
        reasons = [f"{points} في مرور واحد لقمر ناسا، على امتداد {W['km']:.1f} كم",
                   f"مجموع قوتها نحو {_mw(W['mw'])}"]
        if W.get("usual"):
            reasons.append(f"المعتاد هنا نحو {_n(round(W['usual']), 'نقطة', 'نقطتين', 'نقاط', 'نقطة')} في المرور الواحد")
        return _out("wide", reasons + [NO_CAUSE], also)

    # 3) a site that burns most days, now far above its own habit (the existing 'unusual' kind)
    if inc.get("kind") == "unusual":
        reasons = []
        if inc.get("typical_frp") and mw:
            reasons.append(f"معتاد هذا الموقع نحو {_mw(float(inc['typical_frp']))} والآن {_mw(mw)}")
        if inc.get("site_habit"):
            reasons.append(f"أعلى من ضعف ما بلغه في يومين من الشهر ({_mw(float(inc['site_habit']))})")
        return _out("surge", reasons + ["قد يكون حرق غاز تشغيلياً أو حريقاً", NO_CAUSE], [])

    # 4) sustained heat where none burned recently: an unmapped site starting up, a well test, or a
    #    fire (Judah and Abqaiq north, 15 Nov 2026). The same roads as the posts (new_heat_path), but
    #    a single strong burst is not 'sustained'
    hours = _hours(inc)
    if new_heat_path(inc, cfg) in ("a", "b"):
        if quiet is None or known == 0:
            reasons = [f"لم تُرصد حرارة هنا خلال {_n(window, 'يوم', 'يومين', 'أيام', 'يوماً')}"]
        else:
            reasons = [f"آخر حرارة هنا قبل {_n(quiet, 'يوم واحد', 'يومين', 'أيام', 'يوماً')}"]
        reasons.append(_span(hours))
        if pictures:
            reasons.append(f"ظهرت في {_n(pictures, 'صورة واحدة', 'صورتين', 'صور', 'صورة')} لـ Meteosat")
        if PA_OIL_GAS in tags:
            reasons.append("NOAA تصنّف هذه النقطة موقع نفط وغاز")
        return _out("new_sustained", reasons + [NO_CAUSE], also)

    # 5) the rest. «قصيرة أو متقطعة» only for what was brief, or for a place that burned again within
    #    the week or on other days of the month; a long event in an area that burns often is «في منطقة
    #    نشطة»; anything else (not confirmed yet, outside the dry Gulf countries) has no clear pattern
    passes = len(inc.get("passes") or [])
    brief = hours < 3 and pictures < int(cfg["alerts"].get("new_heat_min_pictures", 4))
    again = "quiet_days" in inc and quiet is not None and quiet < int(cfg["alerts"].get("new_heat_quiet_days", 7))
    reasons = []
    if brief:
        if pictures:
            reasons.append(f"ظهرت في {_n(pictures, 'صورة واحدة', 'صورتين', 'صور', 'صورة')} فقط")
        elif passes:
            reasons.append("رآها مرور واحد لقمر ناسا فقط")
    else:
        reasons.append(_span(hours))
    if known:
        reasons.append(f"رُصدت حرارة هنا في {_n(known, 'يوم واحد', 'يومين', 'أيام', 'يوماً')} خلال الشهر")
    elif again:
        reasons.append(f"آخر حرارة هنا قبل {_n(quiet, 'يوم واحد', 'يومين', 'أيام', 'يوماً')}")
    if area >= 2:
        reasons.append(f"المنطقة حولها نشطة (حرارة في {_n(area, 'يوم', 'يومين', 'أيام', 'يوماً')} خلال الشهر)")
    if not _country_ok(inc, cfg):
        reasons.append("خارج دول الخليج الجافة")
    elif inc.get("verdict") != "مؤكد":
        reasons.append("لم تتأكد بعد")
    key = "short" if brief or known or again else "busy" if area >= 2 else "unclear"
    return _out(key, reasons, [])


def duration(hours):
    """«أقل من ساعة», «نحو ساعتين», «نحو 22 ساعة»."""
    if hours < 1:
        return "أقل من ساعة"
    return f"نحو {_n(round(hours), 'ساعة', 'ساعتين', 'ساعات', 'ساعة')}"


def _span(hours):
    return "استمرت " + duration(hours)


def _out(key, reasons, also):
    # the 'several places' tag belongs only to new sustained or wide heat
    return {"key": key, "label": LABELS[key], "reasons": [r for r in reasons if r],
            "also": also if key in LINKABLE else []}


def line(pattern):
    """The neutral reasons line («معلومة» in the app). Posts never use it: main_reason reads '+' only."""
    text = PREFIX + pattern["label"]
    if pattern.get("also"):
        text += " · " + LINKED
    details = [r for r in pattern["reasons"] if r != NO_CAUSE] + list(pattern.get("also") or [])
    if details:
        text += " (" + "، ".join(details) + ")"
    if NO_CAUSE in pattern["reasons"]:
        text += ". " + NO_CAUSE
    return text


def apply(inc, cfg, ev=None):
    """Write the label and its reasons line. `ev`: the assessment's fresh reasons; without it the
    stored ones, any earlier pattern line taken out (a label written again, never twice). The line
    takes the last of the eight places, so that no line before it moves."""
    if ev is None:
        ev = [e for e in inc.get("evidence") or [] if not str(e.get("text", "")).startswith(PREFIX)]
    pattern = pattern_of(inc, cfg)
    if pattern:
        inc["pattern"] = pattern
        ev = ev[:7] + [{"s": "·", "text": line(pattern)}]
    else:
        inc.pop("pattern", None)
    inc["evidence"] = ev[:8]


def label_stored(incidents, cfg):
    """Events made before the patterns (or before their rules changed): their passes rebuilt from the
    NASA readings they kept, and their label written. Nothing else of them changes."""
    for inc in incidents:
        if not inc.get("verdict"):
            continue
        if "passes" not in inc:
            for o in sorted((o for o in inc.get("observations") or [] if o.get("kind") == "polar"),
                            key=lambda o: o["t"]):
                note_pass(inc, o)
        apply(inc, cfg)   # a wide pass seen before stays wide (`inc["wide"]`)


# ----------------------------------------------------------------- several places at once
def link_sites(incidents, cfg, now):
    """Tag new sustained or wide heat that started at another such place within three hours, 5-500 km
    away. Both starts must be known to within a reading (a Meteosat picture of the same product read
    10-40 minutes before showed nothing): after a gap in the readings, every place seems to start at
    once. Plant surges never count (two plants flaring 10 minutes and 162 km apart, Ahmadi and Zubair
    on 15 Nov, is chance). Only events of the last two days are looked at again; an older one keeps
    its tag, and is still found as the partner of a newer one. Returns the incidents whose tag changed."""
    P = cfg.get("patterns") or {}
    hours = float(P.get("link_h", 3))
    lo_km, hi_km = float(P.get("link_min_km", 5)), float(P.get("link_max_km", 500))
    able = [i for i in incidents if (i.get("pattern") or {}).get("key") in LINKABLE and i.get("onset_known") is True]
    changed = []
    for inc in able:
        if now - parse_time(inc["first"]) > dt.timedelta(days=2):
            continue
        best = None
        for other in able:
            if other is inc:
                continue
            minutes = abs((parse_time(other["first"]) - parse_time(inc["first"])).total_seconds()) / 60.0
            if minutes > hours * 60:
                continue
            km = haversine_km(inc["lat"], inc["lon"], other["lat"], other["lon"])
            if lo_km <= km <= hi_km and (best is None or minutes < best["min"]):
                best = {"id": other["id"], "km": round(km), "min": int(round(minutes))}
        if best != inc.get("linked"):
            if best:
                inc["linked"] = best
            else:
                inc.pop("linked", None)
            changed.append(inc)
    return changed

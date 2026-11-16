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
import gzip
import json
import math
import statistics
from pathlib import Path

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


def stretches(inc, max_gap_h=3.5):
    """[(hours, Meteosat pictures)] of each stretch of sightings with no gap over `max_gap_h`. A gap
    is a break only when it is long: Rasad reads about half of Meteosat's slots (none at all from
    18:45 to 20:50 UTC on 15 Nov while Abqaiq and Judah burned on), and Meteosat misses small fires
    by day that NASA sees (Judah on 16 Nov). What two snapshots hours apart lack is pictures, and
    each stretch is judged on its own pictures. Abqaiq north, 15 Nov: gaps of 3.2, 2.3 and 3 hours,
    one stretch of 22 hours with 69 pictures."""
    times = sorted({parse_time(t) for t in inc.get("geo_slots") or []}
                   | {parse_time(o["t"]) for o in inc.get("observations") or [] if o.get("t")}
                   | {parse_time(p["t"]) for p in inc.get("passes") or [] if p.get("t")})
    geo = sorted({parse_time(t) for t in inc.get("geo_slots") or []}
                 | {parse_time(o["t"]) for o in inc.get("observations") or [] if o.get("kind") == "geo"})
    out, start = [], 0
    for k in range(1, len(times) + 1):
        if k == len(times) or (times[k] - times[k - 1]).total_seconds() > max_gap_h * 3600:
            hours = (times[k - 1] - times[start]).total_seconds() / 3600.0
            out.append((hours, sum(1 for t in geo if times[start] <= t <= times[k - 1])))
            start = k
    return out


def near_earlier_heat(incidents, inc, lat, lon, when, reach):
    """Another event at this place that started before `when` and was seen within the day before it:
    the place was not quiet, whatever the memory's earlier days say (it holds whole days, not hours).
    A part of the same pass made into its own event (one NASA pass, two groups 3 km apart) started at
    the same moment: it is not earlier heat."""
    t0 = parse_time(when)
    radius = 2.2 * reach + 2.0
    for other in incidents:
        if other is inc or other.get("id") == inc.get("id"):
            continue
        try:
            first, last = parse_time(other["first"]), parse_time(other["last"])
        except (KeyError, TypeError, ValueError):
            continue
        if first < t0 and t0 - last <= dt.timedelta(days=1) \
                and haversine_km(lat, lon, other["lat"], other["lon"]) <= radius:
            return True
    return False


def new_heat_path(inc, cfg):
    """Which road makes confirmed heat on land in the dry Gulf countries news far from every mapped
    facility (None when none):
    'a'      no heat here nor around for the memory's whole window, 4 pictures (the rule of 15 Nov:
             the pumping station south of Judah);
    'strong' the same quiet place, 20 MW at once: two families already agree (a strong fire is not
             made to wait);
    'b'      quiet for a week, heat on at most two earlier days here, at most one around, and burning
             on in one stretch (no gap over 3.5 hours): 6 hours and 12 pictures, or 3 hours and 4
             pictures where NOAA tags the place oil and gas (Abqaiq north, 15 Nov: confirmed, 22 hours, 60
             pictures, last heat 14 days before, never posted). Only far from mapped facilities:
             near one, the facility rules decide (alert_worthy).
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
        tagged = PA_OIL_GAS in _tags(inc)
        for hours, run_pictures in stretches(inc, float(A.get("new_heat_max_gap_h", 3.5))):
            if (hours >= float(A.get("new_heat_sustained_hours", 6))
                    and run_pictures >= int(A.get("new_heat_sustained_pictures", 12))) \
                    or (tagged and hours >= float(A.get("new_heat_tagged_hours", 3))
                        and run_pictures >= int(A.get("new_heat_min_pictures", 4))):
                return "b"
    return None


def quiet_words(inc):
    """What a quiet-place post may say of the place and of the heat, nothing more: «لم تُرصد حرارة في
    هذا المكان منذ 14 يوماً، ورُصدت فيه على مدى نحو 22 ساعة». 'Since N days' only when an earlier day of
    heat is known; otherwise over the days the memory holds. The span is first to last, said as a span
    (the heat may have stopped by the time it is read)."""
    hours = (parse_time(inc["last"]) - parse_time(inc["first"])).total_seconds() / 3600.0
    quiet = inc.get("quiet_days")
    if inc.get("known_days") and quiet:
        before = f"لم تُرصد حرارة في هذا المكان منذ {_n(quiet, 'يوم واحد', 'يومين', 'أيام', 'يوماً')}"
    else:
        days = int(inc.get("memory_days") or 30)
        before = f"لم تُرصد حرارة في هذا المكان خلال {_n(days, 'يوم', 'يومين', 'أيام', 'يوماً')}"
    return f"{before}، ورُصدت فيه على مدى {duration(hours)}"


def solar_glint(inc, cfg, label=False):
    """NASA's daytime pixels on the panels of a mapped solar plant, strong enough that Meteosat would
    show real heat (50 MW), while a Meteosat picture read at that time showed nothing: sun glint, not
    heat on the ground. The «حريق جديد في الإمارات» post of 11 Nov 2026 (141.6 MW, 15 day pixels
    1.4 km from the Dubai solar park, Meteosat read at 09:20 and 09:30 UTC) was one. Kept out of the
    channel since 16 Nov. Never at an energy facility (one within 2 km, or nearer than the panels) or
    for a plant's surge: Meteosat misses real daytime fires of 10-40 MW there, and Bahrain's Awali
    field, Taweelah or Al Ain power station have panels within 3 km. NOAA's panel tag (a 2017 mask)
    may stand in for the map in the app's label only (`label`), never take a post away."""
    A = cfg["alerts"]
    obs = inc.get("observations") or []
    if inc.get("place") != "land" or not obs or inc.get("geo_slots") or inc.get("geo_read_at_pass") is not True:
        return False
    if inc.get("kind") == "unusual" or not all(o.get("kind") == "polar" and not o.get("night") for o in obs):
        return False
    if float(inc.get("max_frp") or 0) < float(A.get("solar_glint_min_mw", 50)):
        return False
    km = inc.get("solar_km")
    mapped = km is not None and float(km) <= float(A.get("solar_glint_km", 3))
    if not mapped:
        return label and PA_SOLAR in _tags(inc)
    f = inc.get("facility") or {}
    if f.get("km") is not None and (float(f["km"]) <= float(A.get("at_facility_km", 2)) or float(f["km"]) < float(km)):
        return False
    return True


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
    if solar_glint(inc, cfg, label=True):
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
        reasons.append("رُصدت حرارة قربه خلال اليوم السابق" if quiet == 0 else
                       f"آخر حرارة هنا قبل {_n(quiet, 'يوم واحد', 'يومين', 'أيام', 'يوماً')}")
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
        refresh_signals(inc, cfg)


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


# ----------------------------------------------------------------- signals worth attention
# «مؤشرات تستحق الانتباه»: what makes heat at an energy site worth a closer look, and what makes it
# look like the place's everyday heat, each a fact with its numbers. Never a cause: satellites see
# heat, not why (a refinery accident looks like a strike, many strikes leave no heat). Shown in the
# app only. The owner asked for it on 16 Nov 2026 ("how do I know this heat is unusual and from an
# attack?"); the design and its check on real attacks since 2024: docs/plans/heat_patterns.md.
ATLAS_PATH = Path(__file__).resolve().parent / "data" / "heat_atlas.json.gz"
_atlas = None
MONTHS_AR = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول",
             "تشرين الأول", "تشرين الثاني", "كانون الأول"]
SIGNALS_NOTE = ("هذه علامات تستحق النظر وليست حكماً: الأقمار ترى الحرارة ولا ترى سببها، ومعرفة السبب تحتاج مصدراً "
                "آخر كخبر أو بيان رسمي.")


def atlas():
    """The long-term map of usual heat (tools/heat_atlas.py): per ~500 m cell, on how many sampled
    dates since March 2024 NASA's NOAA-20/21 saw heat there. {} when missing."""
    global _atlas
    if _atlas is None:
        try:
            with gzip.open(ATLAS_PATH, "rt", encoding="utf-8") as f:
                _atlas = json.load(f)
        except (OSError, ValueError):
            _atlas = {}
    return _atlas


def _cells_near(A, lat, lon, km):
    """(count, distance km) of every atlas cell whose centre is within km of the point."""
    cell, cells = float(A["cell"]), A["cells"]
    i0, j0 = math.floor(lat / cell), math.floor(lon / cell)
    ri = int(math.ceil(km / (111.0 * cell))) + 1
    rj = int(math.ceil(km / (111.0 * max(0.2, math.cos(math.radians(lat))) * cell))) + 1
    for di in range(-ri, ri + 1):
        for dj in range(-rj, rj + 1):
            n = cells.get(f"{i0 + di},{j0 + dj}")
            if not n:
                continue
            d = haversine_km(lat, lon, (i0 + di + 0.5) * cell, (j0 + dj + 0.5) * cell)
            if d <= km:
                yield n, d


def spot_count(A, lat, lon, km):
    """The most sampled dates any cell within km of the point was hot on (0: never twice)."""
    return max((n for n, _ in _cells_near(A, lat, lon, km)), default=0)


def usual_flare_km(A, lat, lon, km, share):
    """The distance to the nearest spot hot on at least `share` of the sampled dates, within km."""
    need = share * float(A.get("dates") or 0)
    return min((d for n, d in _cells_near(A, lat, lon, km) if n >= need), default=None)


SPOT_EPOCH = dt.date(2024, 1, 1)


def _day_no(day):
    return (dt.date.fromisoformat(day) - SPOT_EPOCH).days


def record_spot(state, det, day, keep_days=180):
    """Rasad's own record of NASA's hot ~500 m spots near energy sites, kept between rebuilds of the
    long-term map: per cell [days with heat, last day, first day] (days counted from 2024-01-01). A
    flare that started after the map was built is learnt within days, not called rare for ever."""
    try:
        lat, lon = float(det["lat"]), float(det["lon"])
        n = _day_no(day)
    except (KeyError, TypeError, ValueError):
        return
    spots = state.setdefault("spots", {})
    key = f"{math.floor(lat / 0.005)},{math.floor(lon / 0.005)}"
    row = spots.get(key)
    if row is None:
        spots[key] = [1, n, n]
    elif n > row[1]:                     # a new day (a late pixel of an earlier day is not counted again)
        row[0] += 1
        row[1] = n
    elif n < row[2]:
        row[2] = n
    if n % 7 == 0 and state.get("spots_pruned") != n:
        # once a week: forget spots quiet for half a year, and one-off spots (1-2 days) after a month
        state["spots_pruned"] = n
        for k in [k for k, r in spots.items() if n - r[1] > keep_days or (r[0] <= 2 and n - r[1] > 30)]:
            del spots[k]


def spot_days(state, lat, lon, before_day, km=0.75):
    """On how many days Rasad saw NASA heat within km of the point, counting only spots first seen
    before `before_day` (a spot that only this event has lit does not make itself usual)."""
    spots = state.get("spots") or {}
    if not spots:
        return 0
    limit = _day_no(before_day)
    cell = 0.005
    i0, j0 = math.floor(lat / cell), math.floor(lon / cell)
    ri = int(math.ceil(km / (111.0 * cell))) + 1
    rj = int(math.ceil(km / (111.0 * max(0.2, math.cos(math.radians(lat))) * cell))) + 1
    best = 0
    for di in range(-ri, ri + 1):
        for dj in range(-rj, rj + 1):
            row = spots.get(f"{i0 + di},{j0 + dj}")
            if row and row[2] < limit and haversine_km(lat, lon, (i0 + di + 0.5) * cell, (j0 + dj + 0.5) * cell) <= km:
                best = max(best, int(row[0]))
    return best


def _since(A):
    try:
        d = dt.date.fromisoformat(A["first"])
        return f"{MONTHS_AR[d.month - 1]} {d.year}"
    except (KeyError, TypeError, ValueError):
        return "2024"


def sudden_start(inc):
    """The strongest Meteosat reading within half an hour of the first sighting, once that half hour
    is over and its readings are known; kept on the event (`start_mw`), never measured again on a
    trimmed series. None when the first sighting was not Meteosat's fire product, or not known yet."""
    if "start_mw" in inc:
        return inc["start_mw"]
    obs = sorted(inc.get("observations") or [], key=lambda o: o.get("t") or "")
    if not obs:
        return None
    first = obs[0]
    if first.get("kind") != "geo" or first.get("src") not in ("mtg_frp", "iodc_fire"):
        inc["start_mw"] = None
        return None
    t0 = parse_time(inc["first"])
    window = [o for o in obs if o.get("kind") == "geo" and parse_time(o["t"]) - t0 <= dt.timedelta(minutes=30)]
    later = [o for o in obs if parse_time(o["t"]) - t0 > dt.timedelta(minutes=30)]
    if not later:
        return None                      # the half hour is not over yet
    values = [float(o["frp"]) for o in window if o.get("frp") is not None]
    inc["start_mw"] = round(max(values), 1) if values else None
    return inc["start_mw"]


def refresh_signals(inc, cfg):
    """Write the signals again (after a before/after picture or a 'several places' tag arrives)."""
    signals = signals_of(inc, cfg)
    if signals:
        inc["signals"] = signals
    else:
        inc.pop("signals", None)


def signals_of(inc, cfg, A=None):
    """{"warn": [...], "calm": [...], "wait": [...], "n": len(warn), "note": ...} for heat on land at
    or near an energy site, else None. Each line is a fact with its numbers:
    warn  heat at a spot of the site that rarely burns (NASA's pixel, ~400 m), while its usual flares
          are elsewhere; a sudden strong start at a quiet place; several places at once; heat spread
          wide in one pass; a change in the clear picture after it (Sentinel-2);
    calm  heat at a spot that burns on many days (the site's own flare); a site that reached such
          levels before this month;
    wait  only Meteosat saw it (~3 km): which spot of the site burns is not known yet."""
    SG = cfg.get("signals") or {}
    if not SG.get("enabled", True) or inc.get("place") != "land" or inc.get("category") == "candidate":
        return None
    A = atlas() if A is None else A
    has_atlas = bool(A) and int(A.get("dates") or 0) >= int(SG.get("min_atlas_dates", 50))
    fac = inc.get("facility") or {}
    at_site = fac.get("km") is not None and float(fac["km"]) <= float(SG.get("facility_km", 3))
    flare_km = usual_flare_km(A, inc["lat"], inc["lon"], float(SG.get("flare_near_km", 5)),
                              float(SG.get("usual_share", 0.10))) if has_atlas else None
    if not (in_scope(inc, cfg) or at_site or flare_km is not None):
        return None
    warn, calm, wait = [], [], []
    dates = int(A.get("dates") or 0) if has_atlas else 0

    # 1) which spot burns: NASA's VIIRS pixels (375 m) against the long-term map and Rasad's own record.
    #    MODIS (1 km and more) is too coarse to say which spot of a site burns.
    viirs = [o for o in inc.get("observations") or []
             if o.get("kind") == "polar" and o.get("lat") is not None and str(o.get("src", "")).startswith("VIIRS")]
    modis_only = not viirs and any(o.get("kind") == "polar" for o in inc.get("observations") or [])
    if has_atlas and (at_site or flare_km is not None):
        if viirs:
            r = float(SG.get("spot_km", 0.75))
            rare_max = float(SG.get("rare_share", 0.02)) * dates
            usual_need = float(SG.get("usual_share", 0.10)) * dates
            looks = []           # (atlas dates, Rasad's days, class) per pixel
            for o in viirs:
                c = spot_count(A, float(o["lat"]), float(o["lon"]), r)
                s_days = int(o.get("spot") or 0)
                cls = ("rare" if c <= rare_max and s_days <= int(SG.get("rare_recent_days", 2)) else
                       "usual" if c >= usual_need or s_days >= int(SG.get("usual_recent_days", 10)) else "sometimes")
                looks.append((c, s_days, cls, o))
            rare = [x for x in looks if x[2] == "rare"]
            if rare:
                o = rare[0][3]
                elsewhere = usual_flare_km(A, float(o["lat"]), float(o["lon"]), float(SG.get("flare_near_km", 5)),
                                           float(SG.get("usual_share", 0.10)))
                text = f"الحرارة في نقطة لم تظهر فيها حرارة إلا نادراً منذ {_since(A)}"
                if elsewhere is not None:
                    text += f"، بينما أقرب مكان تظهر فيه حرارة عادةً على بعد {elsewhere:.1f} كم"
                # counted only for heat worth a look: confirmed or likely, and 5 MW or more. A faint speck at
                # an odd spot (on the week of 9-16 Nov 2026, most were 1-6 MW and seen once) is said, not counted
                if inc.get("verdict") in ("مؤكد", "مرجّح") and float(inc.get("max_frp") or 0) >= float(SG.get("odd_min_mw", 5)):
                    warn.append({"key": "odd_spot", "text": text})
                else:
                    wait.append({"key": "odd_spot_weak", "text": text + "؛ لكنها ضعيفة أو لم تتأكد بعد"})
            elif all(x[2] == "usual" for x in looks):
                weakest = min(looks, key=lambda x: (x[0], x[1]))
                if weakest[0] >= usual_need:
                    calm.append({"key": "usual_spot", "text": "الحرارة في مكان تظهر فيه حرارة عادةً (شعلة أو مصنع): في نحو "
                                                              f"{round(100.0 * weakest[0] / dates)}% من الأيام منذ {_since(A)}"})
                else:
                    calm.append({"key": "usual_spot", "text": "الحرارة في مكان تظهر فيه حرارة عادةً: رأى رصد حرارة هنا في "
                                                              f"{_n(weakest[1], 'يوم واحد', 'يومين', 'أيام', 'يوماً')} خلال الأشهر الأخيرة"})
            else:
                odd = min((x for x in looks if x[2] == "sometimes"), key=lambda x: (x[0], x[1]))
                share = round(100.0 * odd[0] / dates)
                if share >= 1:
                    text = f"تظهر حرارة في هذه النقطة أحياناً (في نحو {share}% من الأيام منذ {_since(A)})"
                else:
                    text = (f"ظهرت حرارة في هذه النقطة في {_n(odd[1], 'يوم واحد', 'يومين', 'أيام', 'يوماً')} مؤخراً، "
                            "ونادراً قبل ذلك")
                calm.append({"key": "sometimes", "text": text})
        elif modis_only:
            wait.append({"key": "spot_unknown", "text": "رآه قمر MODIS التابع لناسا وحده (دقته نحو 1 كم)، فلا يُعرف بعدُ من أيّ "
                                                        "نقطة في المكان تأتي الحرارة؛ قد يتضح ذلك عند مرور قمر VIIRS"})
        else:
            wait.append({"key": "spot_unknown", "text": "رأتها صور Meteosat وحدها (دقتها نحو 3 كم)، فلا يُعرف بعدُ من أيّ نقطة "
                                                        "في المكان تأتي الحرارة؛ قد يتضح ذلك عند مرور قمر ناسا"})

    # 2) a sudden strong start at a quiet place: decided once, from the first half hour after the first
    #    sighting, and kept (`inc["start_mw"]`): the strength series keeps only its last 40 readings
    start = sudden_start(inc)
    if start and inc.get("kind") == "new" and inc.get("onset_known") is True and inc.get("quiet_days") != 0 \
            and start >= float(SG.get("sudden_mw", 20)):
        warn.append({"key": "sudden", "text": f"بدأت فجأة وبقوة: {_mw(start)} خلال أول نصف ساعة من ظهورها، ولم تظهر في "
                                              "صورة Meteosat السابقة لها"})

    # 3) several places at once, 4) spread wide in one pass (the heat patterns)
    if (inc.get("pattern") or {}).get("key") in LINKABLE:
        for line in (inc.get("pattern") or {}).get("also") or []:
            warn.append({"key": "several", "text": line})
    if inc.get("wide"):
        W = inc["wide"]
        warn.append({"key": "wide", "text": f"انتشرت على {_n(W['n'], 'نقطة ساخنة', 'نقطتين ساخنتين', 'نقاط ساخنة', 'نقطة ساخنة')}"
                                            f" على امتداد {W['km']:.1f} كم في مرور واحد لقمر ناسا"})

    # the site's habit: heat there on most days (a flare or a plant); a level it reached before
    mw = float(inc.get("max_frp") or 0)
    if inc.get("kind") == "unusual" and inc.get("known_days"):
        calm.append({"key": "daily", "text": "في هذا المكان حرارة شبه يومية (شعلة أو مصنع): ظهرت في "
                                             f"{_n(int(inc['known_days']), 'يوم واحد', 'يومين', 'أيام', 'يوماً')} من الشهر الماضي"})
    if inc.get("kind") == "unusual" and inc.get("site_record") and float(inc["site_record"]) >= mw > 0:
        calm.append({"key": "record", "text": f"بلغت حرارة هذا المكان {_mw(float(inc['site_record']))} في يوم سابق من الشهر، "
                                              f"وهي الآن {_mw(mw)}"})

    if not (warn or calm or wait):
        return None
    return {"warn": warn, "calm": calm, "wait": wait, "n": len(warn), "note": SIGNALS_NOTE}

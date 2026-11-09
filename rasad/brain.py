"""The judgement layer: remembers normal heat, groups detections into incidents,
decides how sure we are, and writes everything in plain Arabic."""
from __future__ import annotations

import datetime as dt
import math
import re
import statistics

from .util import esc, haversine_km, local_text, parse_time, size_word
from .updates import latest_reading, peak_reading

CELL = 0.02  # ~2 km memory cells

FAMILY_AR = {"mtg": "أوروبي", "msg_iodc": "أوروبي ثانٍ", "viirs": "أمريكي", "modis": "أمريكي قديم",
             "landsat": "أمريكي عالي الدقة"}
VERDICT_RANK = {"يحتاج تأكيداً": 0, "مرجّح": 1, "مؤكد": 2}
VERDICT_ICON = {"مؤكد": "🔴", "مرجّح": "🟠", "يحتاج تأكيداً": "🟡"}
PLACE_AR = {"sea": "في البحر", "coast": "على الساحل", "land": "على اليابسة", "unknown": ""}
OWN_SEA_SOURCES = frozenset({"mtg_spot", "mtg_day_spot"})
OFFICIAL_GEO_SOURCES = frozenset({"mtg_frp", "iodc_fire"})
NASA_FAMILIES = frozenset({"viirs", "modis", "landsat"})


# ----------------------------------------------------------------- memory of normal heat
class Memory:
    """Per ~2 km cell: the days on which heat was seen and that day's strongest fire (MW)."""

    def __init__(self, cells):
        self.cells = cells  # {"i,j": {"YYYY-MM-DD": max_frp}}

    @staticmethod
    def key(lat, lon):
        return f"{math.floor(lat / CELL)},{math.floor(lon / CELL)}"

    def record(self, lat, lon, day, frp):
        c = self.cells.setdefault(self.key(lat, lon), {})
        c[day] = max(c.get(day, 0.0), float(frp or 0.0))

    def profile(self, lat, lon, before_day, window_days, reach=1):
        """(number of earlier days with heat here, typical daily strongest fire).
        reach = how many ~2 km cells around the point count as 'here'."""
        i, j = math.floor(lat / CELL), math.floor(lon / CELL)
        lo = (dt.date.fromisoformat(before_day) - dt.timedelta(days=window_days)).isoformat()
        per_day = {}
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                for day, frp in self.cells.get(f"{i + di},{j + dj}", {}).items():
                    if lo <= day < before_day:
                        per_day[day] = max(per_day.get(day, 0.0), frp)
        vals = [v for v in per_day.values() if v > 0]
        return len(per_day), (statistics.median(vals) if vals else None)

    def days_known(self, before_day, window_days):
        """How many earlier days the memory actually holds (0 = still learning)."""
        lo = (dt.date.fromisoformat(before_day) - dt.timedelta(days=window_days)).isoformat()
        return len({d for c in self.cells.values() for d in c if lo <= d < before_day})

    def prune(self, today, keep_days):
        lo = (dt.date.fromisoformat(today) - dt.timedelta(days=keep_days)).isoformat()
        for k in list(self.cells):
            c = {d: v for d, v in self.cells[k].items() if d >= lo}
            if c:
                self.cells[k] = c
            else:
                del self.cells[k]


# ----------------------------------------------------------------- incidents
def new_incident(det, kind):
    inc = {"id": f"{det['t'][:16].replace('-', '').replace(':', '').replace('T', '-')}"
                  f"_{det['lat']:.3f}_{det['lon']:.3f}",
            "kind": kind, "first": det["t"], "last": det["t"], "lat": det["lat"], "lon": det["lon"],
            "best": det["kind"], "families": [], "geo_slots": [], "polar": [], "max_frp": None,
            "n": 0, "status": "new", "alerted_verdict": None}
    if det.get("_association"):
        inc["association"] = dict(det["_association"])
        inc["association_uncertain"] = True
    return inc


def add_to_incident(inc, det):
    if association_requires_confirmation(inc) and _association_corroborated(inc, det):
        inc["association_uncertain"] = False
        inc["association_resolved_at"] = det["t"]
    inc["first"] = min(inc["first"], det["t"])
    inc["last"] = max(inc["last"], det["t"])
    inc["n"] += 1
    srcs = inc.setdefault("srcs", [])
    if "own_night_slots" not in inc and set(srcs) == {"mtg_spot"}:
        # Older state stored only the combined geo times. Before another source joins,
        # these times are known to belong to the original night finder alone.
        inc["own_night_slots"] = sorted(set(inc["geo_slots"]))[-60:]
    if det["src"] == "mtg_spot" and det["kind"] == "geo":
        inc["own_night_slots"] = sorted(set(inc.get("own_night_slots") or []) | {det["t"]})[-60:]
    if det["src"] not in srcs:
        srcs.append(det["src"])
    if det["family"] not in inc["families"]:
        inc["families"].append(det["family"])
    # Keep acquisition positions rather than reconstructing them from the displayed
    # incident anchor later. Older state has no such provenance; leave it unknown.
    observation = {key: det[key] for key in ("src", "family", "kind", "t", "lat", "lon")}
    for key in ("frp", "scan", "track", "conf", "night", "instrument", "satellite"):
        if det.get(key) is not None:
            observation[key] = det[key]
    observations = inc.setdefault("observations", [])
    if observation not in observations:
        observations.append(observation)
        inc["observations"] = sorted(observations, key=lambda row: row["t"])[-120:]
    if det["kind"] == "geo" and det["t"] not in inc["geo_slots"]:
        inc["geo_slots"].append(det["t"])
        inc["geo_slots"] = sorted(inc["geo_slots"])[-60:]
    if det["kind"] == "polar":
        reading = {"t": det["t"], "frp": det.get("frp"), "conf": det.get("conf"),
                   "night": det.get("night"), "src": det["src"], "family": det["family"],
                   "lat": det["lat"], "lon": det["lon"]}
        for key in ("scan", "track", "instrument", "satellite"):
            if det.get(key) is not None:
                reading[key] = det[key]
        if reading not in inc["polar"]:
            inc["polar"].append(reading)
        inc["polar"] = inc["polar"][-20:]
        if inc["best"] != "polar":  # NASA's position is accurate to a few hundred metres
            inc["lat"], inc["lon"], inc["best"] = det["lat"], det["lon"], "polar"
    if det.get("frp") is not None:
        inc["max_frp"] = max(inc["max_frp"] or 0.0, det["frp"])
        series = inc.setdefault("frp_series", [])
        row = [det["t"], round(float(det["frp"]), 1), det["family"]]
        if row not in series:
            series.append(row)
            inc["frp_series"] = sorted(series)[-40:]


def _polar_observations(inc):
    """Positioned acquisitions only. Never assign a legacy reading the display anchor."""
    out, seen = [], set()
    for row in list(inc.get("observations") or []) + list(inc.get("polar") or []):
        if row.get("kind", "polar") != "polar":
            continue
        try:
            if not all(math.isfinite(float(row[key])) for key in ("lat", "lon")):
                continue
            parse_time(row["t"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        row = dict(row, lat=float(row["lat"]), lon=float(row["lon"]))
        key = (row.get("src"), row["t"], row["lat"], row["lon"])
        if key not in seen:
            out.append(row)
            seen.add(key)
    return out


def _footprint_radius(row):
    """Approximate half-diagonal (km), with a modest geolocation allowance."""
    try:
        scan, track = float(row["scan"]), float(row["track"])
        if all(math.isfinite(v) and v > 0 for v in (scan, track)):
            return 0.5 * math.hypot(scan, track) + 0.5
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    # Missing metadata cannot turn a coarse MODIS pixel into a precise VIIRS point.
    return 3.0 if row.get("family") == "modis" or str(row.get("src", "")).startswith("MODIS") else 1.0


def _known_polar_confidence(row):
    c = str(row.get("conf") if row.get("conf") is not None else "").strip().lower()
    if c in {"n", "nominal", "h", "high"}:
        return True
    try:
        value = float(c)
        return math.isfinite(value) and 40 <= value <= 100
    except ValueError:
        return False


def association_requires_confirmation(inc):
    """A nearby acquisition cannot yet be identified as a new or continuing event."""
    return bool(inc.get("association_uncertain"))


def _association_corroborated(inc, det):
    if det.get("kind") != "polar" or not _known_polar_confidence(det):
        return False
    for prior in _polar_observations(inc):
        if not _known_polar_confidence(prior):
            continue
        gap = abs((parse_time(det["t"]) - parse_time(prior["t"])).total_seconds()) / 60
        different_source = bool(det.get("src") and prior.get("src") and det["src"] != prior["src"])
        different_platform = bool(det.get("satellite") and prior.get("satellite")
                                  and det["satellite"] != prior["satellite"])
        if not different_source and not different_platform and gap < 60:
            continue  # neighbouring pixels of one pass cannot corroborate each other
        reach = min(2.0, _footprint_radius(det) + _footprint_radius(prior))
        if haversine_km(det["lat"], det["lon"], prior["lat"], prior["lon"]) <= reach:
            return True
    return False


def find_incident(incidents, det, merge_km, merge_hours, rules=None, acquisition=None):
    t = parse_time(det["t"])
    best, best_d = None, merge_km
    association, association_distance = None, float("inf")
    rules = rules or {}
    max_gap = float(rules.get("max_polar_gap_hours", 3))
    relocation = float(rules.get("polar_relocation_km", 2))
    members = acquisition if acquisition and det.get("kind") == "polar" else None
    current_lat, current_lon = float(det["lat"]), float(det["lon"])
    if members:
        current_lat = sum(float(row["lat"]) for row in members) / len(members)
        anchor = float(members[0]["lon"])
        current_lon = anchor + sum((float(row["lon"]) - anchor + 180) % 360 - 180
                                   for row in members) / len(members)
        current_lon = (current_lon + 180) % 360 - 180
    det.pop("_association", None)
    for inc in incidents:
        if abs((t - parse_time(inc["last"])).total_seconds()) > merge_hours * 3600 \
                and abs((t - parse_time(inc["first"])).total_seconds()) > merge_hours * 3600:
            continue
        d = haversine_km(current_lat, current_lon, inc["lat"], inc["lon"])
        if d > merge_km:
            continue
        observations = _polar_observations(inc) if det.get("kind") == "polar" else []
        if observations:
            nearest_time = min(observations, key=lambda row: abs((t - parse_time(row["t"])).total_seconds()))
            gap_h = abs((t - parse_time(nearest_time["t"])).total_seconds()) / 3600
            if members:
                # Compare complete acquisition centroids, rather than a centroid
                # that changes while rows of the incoming pass are being added.
                cluster = [row for row in observations if row["t"] == nearest_time["t"]
                           and row.get("src") == nearest_time.get("src")
                           and row.get("satellite") == nearest_time.get("satellite")]
            else:
                cluster = [row for row in observations if abs((parse_time(row["t"]) -
                           parse_time(nearest_time["t"])).total_seconds()) <= max_gap * 3600]
            lat = sum(row["lat"] for row in cluster) / len(cluster)
            if members:
                prior_anchor = cluster[0]["lon"]
                lon = prior_anchor + sum((row["lon"] - prior_anchor + 180) % 360 - 180
                                         for row in cluster) / len(cluster)
                lon = (lon + 180) % 360 - 180
            else:
                lon = sum(row["lon"] for row in cluster) / len(cluster)
            shift = haversine_km(current_lat, current_lon, lat, lon)
            overlap = any(haversine_km(float(current["lat"]), float(current["lon"]), row["lat"], row["lon"]) <=
                          min(merge_km, _footprint_radius(current) + _footprint_radius(row))
                          for current in (members or [det]) for row in observations)
            if not overlap or (gap_h > max_gap and shift > relocation):
                if d < association_distance:
                    association_distance = d
                    association = {"previous_id": inc["id"], "distance_km": round(shift, 2),
                                   "gap_hours": round(gap_h, 2),
                                   "reason": "nearby acquisition after a gap or outside the prior pixel footprint"}
                continue
        elif det.get("kind") == "polar" and inc.get("best") == "polar":
            # Old state retained only an incident anchor, not individual pixel positions.
            # Use it as weak association context for NEW acquisitions, without assigning
            # it to old measurements or splitting those historical records.
            gap_h = abs((t - parse_time(inc.get("last") or inc["first"])).total_seconds()) / 3600
            if gap_h > max_gap and d > relocation:
                if d < association_distance:
                    association_distance = d
                    association = {"previous_id": inc["id"], "distance_km": round(d, 2),
                                   "gap_hours": round(gap_h, 2), "legacy_positions_unknown": True,
                                   "reason": "nearby acquisition after a gap; prior source positions unavailable"}
                continue
        if d <= best_d:
            best, best_d = inc, d
    if best is None and association is not None:
        det["_association"] = association
    return best


def count_ar(n, one, two, many):
    return one if n == 1 else two if n == 2 else f"{n} {many}"


def verdict_of(inc):
    fams = inc["families"]
    slots = len(inc["geo_slots"])
    names = "، ".join(sorted({FAMILY_AR.get(f, f) for f in fams}))
    srcs = set(inc.get("srcs") or [])
    if association_requires_confirmation(inc):
        return "يحتاج تأكيداً", ("رصدة حرارية قرب حدث سابق؛ لم يتضح إن كانت امتداداً له أو حدثاً منفصلاً، "
                                "وننتظر رصداً مستقلاً في موقعها")
    if day_requires_confirmation(inc):
        # Daylight can brighten the short-wave channel through reflected sunlight.
        # Repeated pictures or a weak/unknown NASA detection cannot establish fire.
        return "يحتاج تأكيداً", "بقعة حرارية نهارية تجريبية، تحتاج تأكيداً من مصدر مستقل"
    if "mtg_day_spot" in srcs and not _day_corroborated(inc):
        # Experimental day pictures cannot weaken established night evidence or
        # promote it through their own slot count or low-confidence polar families.
        night_slots = len(set(inc.get("own_night_slots") or []))
        return "مرجّح", (f"وجده الباحث الحراري ليلاً في "
                         f"{count_ar(night_slots, 'صورة', 'صورتين متتاليتين', 'صور متتالية')} "
                         "للقمر الأوروبي، ولم يؤكده قمر آخر بعد")
    if len(fams) >= 2:
        return "مؤكد", f"رصده {count_ar(len(fams), 'قمر', 'قمران مختلفان', 'أقمار مختلفة')} ({names})"
    if sea_finder_doubt(inc):
        return "يحتاج تأكيداً", "وجده الباحث الحراري في صورتين، لكنه لم يظهر بوضوح عند التدقيق"
    if slots >= 2 and srcs and srcs <= OWN_SEA_SOURCES:
        # Rasad's own sea finder alone, on one satellite's pictures: those pictures sometimes carry
        # small blocks and streaks that look like new heat, so it is never "confirmed" by itself
        return "مرجّح", (f"وجده الباحث الحراري في {count_ar(slots, 'صورة', 'صورتين متتاليتين', 'صور متتالية')} "
                         "للقمر الأوروبي، ولم يؤكده قمر آخر بعد")
    if slots >= 3:
        span = (parse_time(inc["geo_slots"][-1]) - parse_time(inc["geo_slots"][0])).total_seconds() / 60
        return "مؤكد", f"ظهر في {slots} صور متتالية خلال {span:.0f} دقيقة"
    if slots == 2:
        span = (parse_time(inc["geo_slots"][-1]) - parse_time(inc["geo_slots"][0])).total_seconds() / 60
        return "مرجّح", ("ظهر في صورتين متتاليتين" if span <= 15 else f"ظهر في صورتين خلال {span:.0f} دقيقة")
    nc = inc.get("near_confirmed")
    ctx = (f"، وقبل {hours_ar(nc['hours'])} رُصد حريق مؤكد آخر في البحر على بعد {nc['km']:.0f} كم"
           if nc else "")
    strong = any(p.get("conf") in ("h", "high") or (p.get("frp") or 0) >= 10 for p in inc["polar"])
    if strong:
        return "مرجّح", "رصده قمر أمريكي بوضوح" + ctx
    if open_sea_fire(inc):
        night = any(p.get("night") for p in inc["polar"])
        return "مرجّح", ("رصده قمر أمريكي " + ("ليلاً " if night else "") + "في عرض البحر، بعيداً عن أي "
                         "منصة نفطية معروفة، ولم تُسجَّل حرارة في هذا المكان من قبل" + ctx)
    if nc and inc.get("place") == "sea" and any(_clear(p) for p in inc["polar"]):
        return "مرجّح", "رصده قمر أمريكي في البحر" + ctx
    return "يحتاج تأكيداً", "رصدة واحدة حتى الآن، وننتظر الصورة التالية"


def sea_finder_doubt(inc):
    """Only Rasad's sea finder saw it, and the closer look at the pictures did not show it."""
    srcs = set(inc.get("srcs") or [])
    return bool(inc.get("unseen_close")) and bool(srcs) and srcs <= OWN_SEA_SOURCES


def day_source_only(inc):
    """Only the experimental daytime sea finder has observed this incident."""
    srcs = set(inc.get("srcs") or [])
    return bool(srcs) and srcs <= {"mtg_day_spot"}


def day_requires_confirmation(inc):
    """Daytime evidence alone cannot establish a fire or erase valid night evidence."""
    return ("mtg_day_spot" in (inc.get("srcs") or [])
            and not _day_corroborated(inc) and not _night_finder_supported(inc))


def _night_finder_supported(inc):
    """Two distinct night observations retain the existing probable-fire verdict."""
    return ("mtg_spot" in (inc.get("srcs") or []) and not inc.get("unseen_close")
            and len(set(inc.get("own_night_slots") or [])) >= 2)


def _day_corroborated(inc):
    """Daylight candidates need an official fire product or clear NASA evidence."""
    if set(inc.get("srcs") or []) & OFFICIAL_GEO_SOURCES:
        return True
    if not set(inc.get("families") or []) & NASA_FAMILIES:
        return False
    for p in inc.get("polar") or []:
        c = str(p.get("conf") if p.get("conf") is not None else "").strip().lower()
        if c in {"n", "nominal", "h", "high"}:
            return True
        try:
            if float(c) >= 40:
                return True
        except ValueError:
            pass
    return False


def _clear(p):
    """A NASA detection that is not marked low confidence (VIIRS: l/n/h; MODIS: 0-100)."""
    c = str(p.get("conf") or "").strip().lower()
    if c in ("l", "low"):
        return False
    return int(c) >= 40 if c.isdigit() else True


def open_sea_fire(inc):
    """Out at sea, away from platforms and with no earlier heat there, a NASA hotspot has few
    innocent explanations: at night none (no sun glint, no fields, no flare stacks); by day it has
    to be a clear detection of some strength (ships hit in the Strait of Hormuz burn by day too)."""
    if inc.get("place") != "sea" or inc.get("kind") != "new" or (inc.get("known_days") or 0) > 0:
        return False
    f = inc.get("facility")
    if f and f.get("nearest_km", f["km"]) <= 3:
        return False
    return any(_clear(p) and (p.get("night") or (p.get("frp") or 0) >= 5) for p in inc["polar"])


def nearby_confirmed(inc, incidents, km=25.0, hours=24.0):
    """A confirmed fire at sea close by in the hours before this one (another ship hit nearby)."""
    if inc.get("place") != "sea":
        return None
    t0 = parse_time(inc["first"])
    best = None
    for o in incidents:
        if o is inc or o.get("verdict") != "مؤكد" or o.get("place") != "sea":
            continue
        gap = (t0 - parse_time(o["last"])).total_seconds() / 3600
        if not 0 < gap <= hours:
            continue
        d = haversine_km(inc["lat"], inc["lon"], o["lat"], o["lon"])
        if d <= km and (best is None or d < best["km"]):
            best = {"km": round(d, 1), "hours": round(gap, 1), "id": o["id"]}
    return best


def hours_ar(h):
    n = int(round(h))
    if n < 1:
        return "أقل من ساعة"
    return "ساعة" if n == 1 else "ساعتين" if n == 2 else f"{n} ساعات" if n <= 10 else f"{n} ساعة"


def headline(inc, cfg):
    f = inc.get("facility")
    region = inc.get("region_name")
    near = float(cfg["alerts"]["near_facility_km"])
    if association_requires_confirmation(inc):
        return ("بقعة حرارية في البحر قرب حدث سابق" if inc.get("place") == "sea"
                else "بقعة حرارية قرب حدث سابق")
    if day_requires_confirmation(inc):
        return "بقعة حرارية نهارية في البحر" + (f" قرب {region}" if region else "")
    if inc["kind"] == "unusual":
        return f"حرارة غير معتادة في {f['label']}" if f else "حرارة غير معتادة في موقع صناعي"
    if f and f["km"] < 0.8:
        # new heat right on a platform is often a flare being lit: say heat, not fire
        return f"حرارة عند {f['label']}" if f.get("code") == "platform" else f"حريق في {f['label']}"
    if inc["place"] == "sea":
        return "حريق في البحر" + (f" قرب {region}" if region else "")
    if f and f["km"] <= near:
        return f"حريق قرب {f['label']}"
    if inc["place"] == "coast":
        return "حريق على الساحل" + (f" في {inc['country']}" if inc.get("country") else "")
    return "حريق جديد" + (f" في {inc['country']}" if inc.get("country") else "")


def alert_worthy(inc, cfg):
    """Important = what the watch is for: fires at sea or in ports, at energy facilities,
    and big fires. Small fires a few km from a facility are usually fields or rubbish."""
    A = cfg["alerts"]
    f = inc.get("facility")
    frp = inc.get("max_frp") or 0
    big = float(A["big_fire_mw"])
    has_geo = bool(inc["geo_slots"])
    # a faint single speck (hot exhaust, a small flare) is not a fire worth a look on its own
    faint = not has_geo and frp < float(A.get("faint_mw", 3))
    weak_only = (not has_geo and bool(inc["polar"]) and frp < 5
                 and all(p.get("conf") in ("l", "low") for p in inc["polar"]))
    seen_before = (inc.get("known_days") or 0) > 0
    if sea_finder_doubt(inc):
        return False
    if inc["kind"] == "unusual":
        return True
    if inc["place"] in ("sea", "coast"):
        return not weak_only and not (faint and seen_before)
    if f and f["km"] <= float(A.get("at_facility_km", 2)):
        return not faint and not weak_only
    # away from facilities, a fire where fires are common (fields burnt every season, flare
    # fields) is not news, however big: Khuzestan's sugar-cane burning is the classic case
    busy = (inc.get("area_days") or 0) >= int(A.get("busy_area_days", 2))
    if busy:
        return False
    if f and f["km"] <= float(A["near_facility_km"]) and frp >= float(A.get("near_facility_min_mw", big)):
        return True
    return frp >= float(A.get("big_fire_far_mw", big)) and in_focus(inc, cfg)


def in_focus(inc, cfg):
    """Big fires far from any facility matter only in the countries being watched
    (the boxes of the Red Sea and Iraq also cover Ethiopia, Sudan, Syria, Turkey...)."""
    names = cfg["alerts"].get("focus_countries") or []
    country = inc.get("country") or ""
    return not names or not country or any(n in country for n in names)


def finalize(inc, cfg):
    """Fill the display fields the app and the alerts use."""
    inc["verdict"], inc["reason"] = verdict_of(inc)
    if inc["kind"] == "unusual" and inc.get("typical_frp") and inc.get("max_frp"):
        ratio = round(inc["max_frp"] / max(inc["typical_frp"], 1.0))
        times = count_ar(ratio, "ضعف", "ضعفين", "أضعاف") if ratio <= 10 else f"{ratio} ضعفاً"
        when = "وقت الرصد" if inc.get("recovered_historical") else "الآن"
        inc["reason"] = f"في هذا المكان حرارة يومية معتادة، لكنها {when} أعلى بنحو {times}. " + inc["reason"]
    inc["size"] = size_word(inc.get("max_frp"))
    inc["headline"] = headline(inc, cfg)
    if inc.get("recovered_historical"):
        inc["headline"] = "رصد سابق مسترجع: " + inc["headline"]
    if inc.get("correction_note"):
        inc["reason"] = inc["correction_note"] + " " + inc["reason"]
    inc["priority"] = "high" if alert_worthy(inc, cfg) else "low"
    inc["start_text"] = local_text(parse_time(inc["first"]), cfg)
    inc["last_text"] = local_text(parse_time(inc["last"]), cfg)
    inc["sources_text"] = "، ".join(sorted({FAMILY_AR.get(f, f) for f in inc["families"]}))
    return inc


def links(lat, lon, sea):
    out = {"الخريطة": f"https://www.google.com/maps?q={lat:.5f},{lon:.5f}&t=k"}
    if sea:
        out["السفن"] = f"https://www.marinetraffic.com/en/ais/home/centerx:{lon:.3f}/centery:{lat:.3f}/zoom:11"
    out["ناسا"] = f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lon:.3f},{lat:.3f},11.0z"
    return out


_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.'&/\- ]*[A-Za-z0-9.]|[A-Za-z]")


def bidi_safe(text):
    """Keep Latin names (OpenStreetMap names) whole inside Arabic lines: each Latin run is set
    apart (FSI ... PDI) and followed by a right-to-left mark, so brackets and numbers around it
    stay in place in every Telegram app."""
    return _LATIN.sub(lambda m: "\u2068" + m.group(0) + "\u2069\u200f", str(text or ""))


SOURCE_TG = {"viirs": "ناسا", "modis": "ناسا", "landsat": "لاندسات", "mtg": "ميتيوسات الأوروبي",
             "msg_iodc": "ميتيوسات الأوروبي"}


def app_link(cfg, inc):
    """Opens the app straight on this incident (the query survives every Telegram app)."""
    base = cfg["app_url"].split("#")[0].split("?")[0]
    return f"{base}?e={inc['id']}#{inc['id']}"


def mw_ar(v):
    return (f"{v:.1f}" if v < 10 else f"{v:.0f}") + " MW"


def _strength_lines(inc, cfg, intensity=False, with_size=False, with_date=False):
    peak, latest = peak_reading(inc), latest_reading(inc)
    when, mw = peak if peak is not None else (None, None)
    try:
        stored = float(inc.get("max_frp"))
        if math.isfinite(stored) and stored >= 0 and (mw is None or stored > mw):
            when, mw = None, stored
    except (TypeError, ValueError, OverflowError):
        pass
    if mw is None:
        return []
    text = f"🔥 <b>أعلى شدة مرصودة:</b> {mw_ar(mw)}"
    if when:
        text += " · " + local_text(parse_time(when), cfg, with_date=with_date)
    if with_size and inc.get("size"):
        text += f" · {inc['size']}"
    lines = [text]
    if intensity:
        try:
            previous = float(inc.get("alerted_max_frp"))
            if math.isfinite(previous) and 0 <= previous < mw:
                lines.append(f"📈 <b>منذ آخر تنبيه:</b> من {mw_ar(previous)} إلى {mw_ar(mw)}")
        except (TypeError, ValueError, OverflowError):
            pass
    if latest is not None and latest[1] != mw:
        lines.append(f"🌡 <b>آخر قراءة:</b> {mw_ar(latest[1])} · "
                     + local_text(parse_time(latest[0]), cfg, with_date=with_date))
    return lines


def alert_message(inc, cfg, update=False, threaded=False, intensity=False, video_update=False, delayed=False):
    """The channel post: a bold headline, one fact per line with a label, the coordinates to copy
    and the links on their own lines. Only a confirmed fire is called "مؤكد"; other grades are not
    named. An update that is sent as a reply to the first post only says what changed."""
    sure = inc["verdict"] == "مؤكد"
    lt = cfg["local_time"]
    off = dt.timedelta(hours=float(lt["offset_hours"]))
    start = parse_time(inc["first"]) + off
    now_local = parse_time(inc["last"]) + off
    when = f"{start:%H:%M} بتوقيت {lt['name']}" + ("" if start.date() == now_local.date() else f" · {start:%Y/%m/%d}")
    lines = []
    if update:
        newly_confirmed = sure and inc.get("alerted_verdict") != "مؤكد" and not video_update and not delayed
        if delayed:
            title = "فيديو محدث لقياسات سابقة للحريق" if video_update else "تحديث متأخر لقياسات الحريق"
            lines.append(f"🔄 <b>{title}</b>")
        elif video_update:
            lines.append("🔄 <b>فيديو محدث للحريق</b>")
        elif intensity:
            title = "تحديث: الحريق مؤكد الآن وارتفعت شدته" if newly_confirmed else "تحديث: ارتفاع شدة الحريق"
            lines.append(f"🔄 <b>{title}</b>")
        else:
            lines.append("🔄 <b>تحديث: الحريق مؤكد الآن</b>" if newly_confirmed else "🔄 <b>تحديث</b>")
        if not threaded:
            lines.append(f"<b>{esc(bidi_safe(inc['headline']))}</b>")
        if newly_confirmed:
            lines.append(f"✅ {esc(bidi_safe(inc['reason']))}")
        lines.extend(_strength_lines(inc, cfg, intensity=intensity, with_date=delayed))
        lines.append(f"🕒 <b>آخر رصد:</b> {local_text(parse_time(inc['last']), cfg, with_date=delayed)}")
        if delayed:
            lines.append("القياسات المذكورة سابقة وليست رصداً جديداً الآن")
        if not threaded:
            lines.append(f"<code>{inc['lat']:.4f}, {inc['lon']:.4f}</code>")
        if cfg.get("app_url"):
            lines.append(f'<a href="{app_link(cfg, inc)}">فتح الحدث في التطبيق</a>')
        return "\n".join(lines)
    lines.append(f"{'🔴' if sure else '🟠'} <b>{esc(bidi_safe(inc['headline']))}</b>")
    if sure:
        lines.append(f"✅ <b>مؤكد</b> · {esc(bidi_safe(inc['reason']))}")
    lines.append("")
    lines.append(f"📍 <b>المكان:</b> {esc(bidi_safe(inc['where']))}")
    lines.append(f"🕒 <b>البداية:</b> {when}")
    lines.extend(_strength_lines(inc, cfg, with_size=True))
    f = inc.get("facility")
    if f:
        lines.append(f"🏭 <b>أقرب منشأة:</b> {esc(bidi_safe(f.get('label') or f['cat']))} · على بعد {f['km']:.0f} كم")
    else:
        lines.append(f"🏭 <b>أقرب منشأة:</b> لا منشأة طاقة ضمن {cfg['geodata']['facility_max_km']} كم")
    names = []
    for fam in inc["families"]:
        n = SOURCE_TG.get(fam, FAMILY_AR.get(fam, fam))
        if n not in names:
            names.append(n)
    if names:
        lines.append(f"🛰 <b>رصدته:</b> {' و'.join(names)}")
    nc = inc.get("near_confirmed")
    if nc and not sure:
        lines.append(f"📌 قبل {hours_ar(nc['hours'])} رُصد حريق مؤكد آخر في البحر على بعد {nc['km']:.0f} كم")
    lines.append("")
    lines.append(f"<code>{inc['lat']:.4f}, {inc['lon']:.4f}</code>")
    lk = links(inc["lat"], inc["lon"], inc["place"] in ("sea", "coast"))
    lines.append(" | ".join(f'<a href="{u}">{k}</a>' for k, u in lk.items()))
    if cfg.get("app_url"):
        lines.append(f'📲 <a href="{app_link(cfg, inc)}">فتح الحدث في التطبيق</a>')
    return "\n".join(lines)

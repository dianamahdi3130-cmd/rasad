"""Facilities and places database (OpenStreetMap), place names, sea/land check."""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import requests

from .sources import region_tiles
from .util import (bearing_deg, box_around, direction_ar, haversine_km, iso, km_text, load_json, log,
                   parse_time, save_json, utcnow)

FACILITY_QUERY = """[out:json][timeout:90];
(
  nwr["man_made"="offshore_platform"]({b});
  nwr["industrial"~"^(refinery|oil_refinery|oil|gas|petroleum|petrochemical|liquefied_natural_gas|fuel)$"]({b});
  nwr["man_made"="works"]["product"~"oil|gas|petrol|fuel|lng",i]({b});
  nwr["power"="plant"]({b});
  nwr["water_works"="desalination"]({b});
  nwr["landuse"="port"]({b});
  nwr["industrial"="port"]({b});
  nwr["man_made"="storage_tank"]["content"~"oil|fuel|gas|crude|petrol",i]({b});
  nwr["man_made"="pumping_station"]["substance"~"oil|gas",i]({b});
);
out center tags;"""

PLACE_QUERY = """[out:json][timeout:90];
(
  node["place"~"^(city|town)$"]["name"]({b});
  nwr["place"="island"]["name"]({b});
  node["place"="islet"]["name"]({b});
);
out center tags;"""

POWER_SOURCE_AR = {"gas": "غاز", "oil": "نفط", "diesel": "ديزل", "solar": "شمسية", "nuclear": "نووية",
                   "coal": "فحم", "wind": "رياح", "hydro": "مائية"}


# A fire at a solar, wind or hydro farm is a grass fire, not an energy-infrastructure fire.
SKIP_CATS = ("شمسية", "رياح", "مائية")


def relevant_item(it):
    """Facility index item [lat, lon, name, Arabic category, code] that matters for fires."""
    return not any(w in (it[3] or "") for w in SKIP_CATS)


NOT_ENERGY = ("edible", "food", "vegetable", "cooking", "palm", "olive", "sunflower", "soap", "sesame",
              "زيوت", "زيت الطعام", "صابون", "أغذية")


def facility_category(tags):
    """(Arabic category, short code) for an OSM energy/industrial feature, or None if not energy."""
    mm, ind = tags.get("man_made", ""), tags.get("industrial", "")
    prod = tags.get("product", "").lower()
    names = " ".join(tags.get(k, "") for k in ("name", "name:en", "name:ar", "operator")).lower()
    if any(w in names or w in prod for w in NOT_ENERGY):
        return None
    if mm == "offshore_platform":
        return "منصة نفط بحرية", "platform"
    if ind in ("refinery", "oil_refinery") or "refin" in names or "مصفا" in names:
        return "مصفاة", "refinery"
    if ind == "liquefied_natural_gas" or "lng" in prod or "lng" in names:
        return "منشأة غاز مسال", "lng"
    if ind == "petrochemical" or "petrochem" in names or "بتروكيم" in names:
        return "مصنع بتروكيماويات", "petrochem"
    if tags.get("power") == "plant":
        src = POWER_SOURCE_AR.get(tags.get("plant:source", "").lower())
        return ("محطة كهرباء" + (f" ({src})" if src else "")), "power"
    if mm == "water_works" or tags.get("water_works") == "desalination" or "desal" in names or "تحلية" in names:
        return "محطة تحلية مياه", "desal"
    if mm == "storage_tank":
        return "خزانات وقود", "tanks"
    if mm == "pumping_station":
        return "محطة ضخ نفط وغاز", "pumping"
    if tags.get("landuse") == "port" or ind == "port" or tags.get("harbour") == "yes":
        return "ميناء", "port"
    if ind == "gas" or "gas" in prod or "gas" in names or "غاز" in names:
        return "منشأة غاز", "gas"
    if ind in ("oil", "petroleum", "fuel") or any(k in prod for k in ("oil", "petrol", "fuel")) \
            or any(k in names for k in ("oil", "petrol", "نفط", "بترول")):
        return "منشأة نفطية", "oil"
    return "منشأة صناعية", "industrial"


def osm_name(tags):
    return (tags.get("name:ar") or tags.get("name") or tags.get("name:en") or "").strip()


TYPE_WORDS = ("مصفا", "حقل", "ميناء", "مرفأ", "محطة", "منصة", "مصنع", "شركة", "مجمع", "منشأة")


def facility_label(name, cat):
    """'مصفاة رأس تنورة' style label without repeating the type."""
    if not name:
        return f"{cat} (بدون اسم معروف)"
    if not any("؀" <= ch <= "ۿ" for ch in name):
        return f"{cat} ({name})"
    return name if any(w in name for w in TYPE_WORDS) else f"{cat} {name}"


def overpass(session, urls, query, timeout=100):
    """One attempt per server; a failed tile is simply retried in a later run."""
    for url in urls:
        try:
            r = session.post(url, data={"data": query}, timeout=timeout)
            if r.status_code == 200:
                return r.json().get("elements", [])
            log.warning("Overpass %s: HTTP %s", url, r.status_code)
        except (requests.RequestException, ValueError) as e:
            log.warning("Overpass %s: %s", url, type(e).__name__)
    return None


def _center(el):
    if "lat" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else (None, None)


def _write_db(out_dir, fac, places, built):
    """Write the lookup files (one entry per category per ~1 km, so tank farms count once)."""
    thinned, seen = [], set()
    for lat, lon, name, cat, code in sorted(fac.values(), key=lambda f: (f[2] == "", f[4])):
        k = (code, round(lat, 2), round(lon, 2))
        if k not in seen:
            seen.add(k)
            thinned.append([lat, lon, name, cat, code])
    save_json(out_dir / "facilities.json", {"built": built, "items": thinned}, compact=True)
    save_json(out_dir / "places.json", {"built": built, "items": list(places.values())}, compact=True)
    return len(thinned), len(places)


def geodata_status(cfg):
    """(age in days of the last complete build or None, build in progress?)"""
    d = Path(cfg["_geodata_dir"])
    built = load_json(d / "facilities.json", {}).get("built")
    prog = load_json(d / "progress.json", {})
    age = None if not built else (utcnow() - parse_time(built)).total_seconds() / 86400
    return age, bool(prog.get("done"))


def build_geodata(cfg, session, budget_s=None):
    """Download facilities and places tile by tile; resumable, so it can run in short slices."""
    G = cfg["geodata"]
    out_dir = Path(cfg["_geodata_dir"])
    tiles = region_tiles(cfg["regions"], max_deg=float(G.get("query_tile_deg", 8)))
    keys = [",".join(f"{v:.2f}" for v in t) for t in tiles]
    prog = load_json(out_dir / "progress.json", {})
    if prog.get("tiles") != keys:
        prog = {"tiles": keys, "done": {}, "fac": {}, "place": {}}
    t0, pause, tried = time.time(), float(G.get("overpass_pause_s", 5)), 0
    for i, (key, (w, s, e, n)) in enumerate(zip(keys, tiles), 1):
        if prog["done"].get(key):
            continue
        if budget_s is not None and tried and time.time() - t0 > budget_s:
            break  # at least one tile per run, then stop when the time slice is used
        tried += 1
        b = f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
        ok = True
        for kind, query in (("fac", FACILITY_QUERY), ("place", PLACE_QUERY)):
            if kind == "place" and tried > 1 and budget_s is not None and time.time() - t0 > budget_s * 1.5:
                ok = False
                break
            els = overpass(session, G["overpass_urls"], query.format(b=b))
            if els is None:
                ok = False
                break
            for el in els:
                lat, lon = _center(el)
                if lat is None:
                    continue
                tags = el.get("tags") or {}
                oid = f"{el['type'][0]}{el['id']}"
                if kind == "fac":
                    kind_of = facility_category(tags)
                    if kind_of:
                        prog["fac"][oid] = [round(lat, 5), round(lon, 5), osm_name(tags), *kind_of]
                else:
                    prog["place"][oid] = [round(lat, 5), round(lon, 5), osm_name(tags), tags.get("place", "")]
            time.sleep(pause)
        if ok:
            prog["done"][key] = True
        log.info("facility map: tile %d/%d %s", i, len(tiles), "done" if ok else "failed, retry next run")
        save_json(out_dir / "progress.json", prog, compact=True)
    complete = all(prog["done"].get(k) for k in keys)
    first_build = not (out_dir / "facilities.json").exists() or not load_json(
        out_dir / "facilities.json", {}).get("built")
    if complete:
        nf, npl = _write_db(out_dir, prog["fac"], prog["place"], iso(utcnow()))
        save_json(out_dir / "progress.json", {"tiles": keys, "done": {}, "fac": {}, "place": {}}, compact=True)
        log.info("facility map complete: %d facilities, %d places", nf, npl)
    elif first_build:
        nf, npl = _write_db(out_dir, prog["fac"], prog["place"], None)  # usable while still loading
        log.info("facility map partly loaded: %d/%d tiles", sum(1 for k in keys if prog["done"].get(k)), len(keys))
    return complete


class GeoIndex:
    """Fast nearest lookup on a 1-degree grid."""

    def __init__(self, items):
        self.items, self.grid = items, {}
        for i, it in enumerate(items):
            self.grid.setdefault((math.floor(it[0]), math.floor(it[1])), []).append(i)

    def nearest(self, lat, lon, max_km, accept=None):
        best, best_d = None, max_km
        r = int(math.ceil(max_km / 90.0))
        gi, gj = math.floor(lat), math.floor(lon)
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                for idx in self.grid.get((gi + di, gj + dj), ()):
                    it = self.items[idx]
                    if accept and not accept(it):
                        continue
                    d = haversine_km(lat, lon, it[0], it[1])
                    if d <= best_d:
                        best, best_d = it, d
        return (best, best_d) if best else (None, None)


class Geo:
    def __init__(self, cfg, session, wms, state):
        self.cfg, self.s, self.wms, self.state = cfg, session, wms, state
        d = Path(cfg["_geodata_dir"])
        fac = load_json(d / "facilities.json", {"items": []})
        self.fac = GeoIndex(fac["items"])
        self.places = GeoIndex(load_json(d / "places.json", {"items": []})["items"])
        self.version = f"{fac.get('built')}|{len(fac['items'])}"
        self.broken = False
        self._last_nominatim = 0.0

    @property
    def has_data(self):
        return bool(self.fac.items or self.places.items)

    def platform_within(self, lat, lon, km):
        return self.fac.nearest(lat, lon, km, accept=lambda it: it[4] == "platform")[0] is not None

    def facility_within(self, lat, lon, km):
        """Is there an oil/gas/power/port facility within km? (no network, used while judging)"""
        return self.fac.nearest(lat, lon, km, accept=relevant_item)[0] is not None

    # -- nearest facility -------------------------------------------------------
    def facility(self, lat, lon):
        max_km = float(self.cfg["geodata"]["facility_max_km"])
        it, d = self.fac.nearest(lat, lon, max_km, accept=relevant_item)
        if not it:
            return None
        d_any = d
        if not it[2]:  # prefer a named facility when it is almost as close
            named, dn = self.fac.nearest(lat, lon, min(max_km, d + 2.0),
                                         accept=lambda x: bool(x[2]) and relevant_item(x))
            if named:
                it, d = named, dn
        b = bearing_deg(it[0], it[1], lat, lon)
        label = facility_label(it[2], it[3])
        if d < 0.8:
            text = f"داخل {label} أو ملاصق لها"
        else:
            text = f"{km_text(d)} {direction_ar(b)} {label}"
        return {"name": it[2], "cat": it[3], "code": it[4], "label": label, "km": round(d, 1),
                "nearest_km": round(d_any, 1),
                "dir": direction_ar(b), "lat": it[0], "lon": it[1], "text": text}

    # -- place names ---------------------------------------------------------------
    def reverse(self, lat, lon):
        """Arabic country/province names from OpenStreetMap Nominatim (cached)."""
        key = f"{round(lat, 2)},{round(lon, 2)}"
        cache = self.state.setdefault("geocode", {})
        if key in cache:
            return cache[key]
        G = self.cfg["geodata"]
        wait = 1.1 - (time.time() - self._last_nominatim)
        if wait > 0:
            time.sleep(wait)
        res = {}
        try:
            r = self.s.get(G["nominatim_url"], params={"format": "jsonv2", "lat": f"{lat:.5f}",
                                                         "lon": f"{lon:.5f}", "zoom": 10,
                                                         "accept-language": "ar"}, timeout=30)
            self._last_nominatim = time.time()
            if r.status_code == 200:
                a = (r.json() or {}).get("address") or {}
                res = {"country": a.get("country", ""), "state": a.get("state") or a.get("region", ""),
                       "local": a.get("city") or a.get("town") or a.get("village") or a.get("county", "")}
        except (requests.RequestException, ValueError) as e:
            log.warning("Nominatim: %s", e)
        cache[key] = res
        return res

    def describe(self, lat, lon, place_kind):
        """Plain description such as 'في البحر، على بعد 34 كم جنوب شرق خصب (عُمان)'."""
        G = self.cfg["geodata"]
        big, d_big = self.places.nearest(lat, lon, float(G["place_max_km"]),
                                         accept=lambda it: it[3] in ("city", "town"))
        any_, d_any = self.places.nearest(lat, lon, float(G["place_max_km"]))
        p, d = (any_, d_any) if any_ and (not big or d_any < 5 or d_any < d_big / 3) else (big, d_big)
        here = self.reverse(lat, lon) if place_kind != "sea" else {}
        country = here.get("country") or (self.reverse(p[0], p[1]).get("country") if p else "")
        suffix = f" ({country})" if country else ""
        if p:
            rel = f"{km_text(d)} {direction_ar(bearing_deg(p[0], p[1], lat, lon))} {p[2]}"
            if place_kind == "sea":
                return f"في البحر، على بعد {rel}{suffix}", country
            if d < 2:
                return f"في {p[2]}{suffix}", country
            return f"على بعد {rel}{suffix}", country
        if place_kind == "sea":
            return "في عرض البحر" + suffix, country
        parts = [x for x in (here.get("local"), here.get("state"), country) if x]
        return "، ".join(parts) or "موقع بعيد عن المدن", country

    # -- sea or land ---------------------------------------------------------------
    def place_kind(self, lat, lon):
        """'sea', 'coast' or 'land' (cached). Says 'unknown' rather than guess 'sea'."""
        if self.broken:
            return "unknown"
        key = f"{round(lat, 3)},{round(lon, 3)}"
        cache = self.state.setdefault("landsea", {})
        if key in cache:
            return cache[key]
        coast_km = float(self.cfg["geodata"]["coast_km"])
        kind = "unknown"
        mask = land_mask_points()
        if mask is not None:
            if mask([lat], [lon])[0]:
                kind = "land"
            else:
                lats, lons = ring_points(lat, lon, (0.5, 1.0, coast_km))
                kind = "coast" if mask(lats, lons).any() else "sea"
        else:
            layer = self.cfg["eumetview"].get("land_layer")
            img, _ = (self.wms.getmap(layer, box_around(lat, lon, 25), 200, 200,
                                      style=self.cfg["eumetview"].get("land_style", ""), quiet=True)
                      if layer else (None, None))
            if img is not None:
                m = np.asarray(img)[..., 3] > 0
                if m.any():  # a blank picture proves nothing, so it is not taken as 'sea'
                    c, km_px = 100, 50.0 / 200
                    if m[c, c]:
                        kind = "land"
                    else:
                        ys, xs = np.nonzero(m)
                        d = np.sqrt(((ys - c) * km_px) ** 2 + ((xs - c) * km_px) ** 2).min()
                        kind = "coast" if d <= coast_km else "sea"
        if kind != "unknown":
            cache[key] = kind
        return kind

    def self_test(self):
        """Baghdad must be land and the middle of the Gulf must be sea, or sea/land is switched off."""
        cache = self.state.setdefault("landsea", {})
        cache.pop("33.31,44.36", None)
        cache.pop("26.5,53.0", None)
        ok = self.place_kind(33.31, 44.36) == "land" and self.place_kind(26.5, 53.0) == "sea"
        if not ok:
            self.broken = True
            log.error("sea/land check failed its self-test; places will be reported as unknown")
        return ok


_GLOBE = None


def land_mask_points():
    """Vectorised land test from the global-land-mask package (1 km), or None if unavailable."""
    global _GLOBE
    if _GLOBE is None:
        try:
            from global_land_mask import globe
            _GLOBE = lambda la, lo: np.asarray(globe.is_land(np.asarray(la, float), np.asarray(lo, float)))  # noqa: E731
        except Exception as e:  # noqa: BLE001
            log.warning("global-land-mask not available (%s)", e)
            _GLOBE = False
    return _GLOBE or None


def ring_points(lat, lon, radii_km):
    lats, lons = [], []
    for r in radii_km:
        for ang in range(0, 360, 30):
            a = math.radians(ang)
            lats.append(lat + r * math.cos(a) / 111.32)
            lons.append(lon + r * math.sin(a) / (111.32 * max(math.cos(math.radians(lat)), 0.05)))
    return lats, lons

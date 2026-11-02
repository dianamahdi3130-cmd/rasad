"""Facilities and places database (OpenStreetMap), place names, sea/land check."""
from __future__ import annotations

import datetime as dt
import math
import time
from pathlib import Path

import numpy as np
import requests

from .sources import region_tiles
from .util import (bearing_deg, box_around, direction_ar, haversine_km, km_text, load_json, log,
                   save_json, utcnow, iso)

FACILITY_QUERY = """[out:json][timeout:300];
(
  nwr["man_made"="offshore_platform"]({b});
  nwr["industrial"~"^(refinery|oil_refinery|oil|gas|petroleum|petrochemical|liquefied_natural_gas|fuel)$"]({b});
  nwr["man_made"="works"]["product"~"oil|gas|petrol|fuel|lng",i]({b});
  nwr["power"="plant"]({b});
  nwr["man_made"="water_works"]({b});
  nwr["water_works"="desalination"]({b});
  nwr["landuse"="port"]({b});
  nwr["industrial"="port"]({b});
  nwr["harbour"="yes"]["name"]({b});
  nwr["man_made"="storage_tank"]["content"~"oil|fuel|gas|crude|petrol",i]({b});
  nwr["man_made"="pumping_station"]["substance"~"oil|gas",i]({b});
  nwr["landuse"="industrial"]["name"~"refin|oil|gas|petro|lng|مصفا|نفط|غاز|بترو",i]({b});
);
out center tags;"""

PLACE_QUERY = """[out:json][timeout:300];
(
  node["place"~"^(city|town|village)$"]["name"]({b});
  nwr["place"~"^(island|islet)$"]["name"]({b});
);
out center tags;"""

POWER_SOURCE_AR = {"gas": "غاز", "oil": "نفط", "diesel": "ديزل", "solar": "شمسية", "nuclear": "نووية",
                   "coal": "فحم", "wind": "رياح", "hydro": "مائية"}


def facility_category(tags):
    """(Arabic category, short code) for an OSM energy/industrial feature."""
    mm, ind = tags.get("man_made", ""), tags.get("industrial", "")
    prod = tags.get("product", "").lower()
    names = " ".join(tags.get(k, "") for k in ("name", "name:en", "name:ar", "operator")).lower()
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


def overpass(session, urls, query, tries=3, retry_wait=20):
    for attempt in range(tries):
        for url in urls:
            try:
                r = session.post(url, data={"data": query}, timeout=360)
                if r.status_code == 200:
                    return r.json().get("elements", [])
                log.warning("Overpass %s: HTTP %s", url, r.status_code)
            except (requests.RequestException, ValueError) as e:
                log.warning("Overpass %s: %s", url, e)
        time.sleep(retry_wait * (attempt + 1))
    return None


def _center(el):
    if "lat" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else (None, None)


def build_geodata(cfg, session):
    """Download facilities and places for every enabled region (run weekly)."""
    G = cfg["geodata"]
    tiles = region_tiles(cfg["regions"], max_deg=float(G.get("query_tile_deg", 4.0)))
    facilities, places, failed = {}, {}, 0
    for i, (w, s, e, n) in enumerate(tiles, 1):
        b = f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
        log.info("geodata tile %d/%d", i, len(tiles))
        for kind, query in (("fac", FACILITY_QUERY), ("place", PLACE_QUERY)):
            els = overpass(session, G["overpass_urls"], query.format(b=b),
                           retry_wait=float(G.get("overpass_retry_s", 20)))
            if els is None:
                failed += 1
                continue
            for el in els:
                lat, lon = _center(el)
                tags = el.get("tags") or {}
                if lat is None:
                    continue
                key = f"{el['type'][0]}{el['id']}"
                if kind == "fac":
                    cat, code = facility_category(tags)
                    facilities[key] = [round(lat, 5), round(lon, 5), osm_name(tags), cat, code]
                else:
                    places[key] = [round(lat, 5), round(lon, 5), osm_name(tags), tags.get("place", "")]
            time.sleep(float(G.get("overpass_pause_s", 5)))
    out_dir = Path(cfg["_geodata_dir"])
    built = utcnow()
    fac_items, place_items = list(facilities.values()), list(places.values())
    if failed:
        # keep what we had for the parts that failed, and try again tomorrow
        fac_items += load_json(out_dir / "facilities.json", {"items": []})["items"]
        place_items += load_json(out_dir / "places.json", {"items": []})["items"]
        built -= dt.timedelta(days=max(0.0, float(G.get("rebuild_days", 7)) - 1))
    # thin out tank farms and similar clusters: keep one entry per category per ~1 km
    thinned, seen = [], set()
    for lat, lon, name, cat, code in sorted(fac_items, key=lambda f: (f[2] == "", f[4])):
        k = (code, round(lat, 2), round(lon, 2))
        if k in seen:
            continue
        seen.add(k)
        thinned.append([lat, lon, name, cat, code])
    uniq_places = list({(round(p[0], 4), round(p[1], 4), p[2]): p for p in place_items}.values())
    save_json(out_dir / "facilities.json", {"built": iso(built), "items": thinned}, compact=True)
    save_json(out_dir / "places.json", {"built": iso(built), "items": uniq_places}, compact=True)
    places = uniq_places
    log.info("geodata: %d facilities, %d places (%d failed queries)", len(thinned), len(places), failed)
    return len(thinned), len(places), failed


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
        self.fac = GeoIndex(load_json(d / "facilities.json", {"items": []})["items"])
        self.places = GeoIndex(load_json(d / "places.json", {"items": []})["items"])
        self._last_nominatim = 0.0

    @property
    def has_data(self):
        return bool(self.fac.items or self.places.items)

    # -- nearest facility -------------------------------------------------------
    def facility(self, lat, lon):
        max_km = float(self.cfg["geodata"]["facility_max_km"])
        it, d = self.fac.nearest(lat, lon, max_km)
        if not it:
            return None
        if not it[2]:  # prefer a named facility when it is almost as close
            named, dn = self.fac.nearest(lat, lon, min(max_km, d + 2.0), accept=lambda x: bool(x[2]))
            if named:
                it, d = named, dn
        b = bearing_deg(it[0], it[1], lat, lon)
        label = facility_label(it[2], it[3])
        if d < 0.8:
            text = f"داخل {label} أو ملاصق لها"
        else:
            text = f"{km_text(d)} {direction_ar(b)} {label}"
        return {"name": it[2], "cat": it[3], "code": it[4], "label": label, "km": round(d, 1),
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
        """'sea', 'coast' or 'land' using EUMETView's land polygons (cached)."""
        key = f"{round(lat, 3)},{round(lon, 3)}"
        cache = self.state.setdefault("landsea", {})
        if key in cache:
            return cache[key]
        layer = self.cfg["eumetview"].get("land_layer")
        if not layer:
            return "unknown"
        coast_km = float(self.cfg["geodata"]["coast_km"])
        bbox = box_around(lat, lon, 6)
        img, _ = self.wms.getmap(layer, bbox, 96, 96, style=self.cfg["eumetview"].get("land_style", ""))
        if img is None:
            return "unknown"
        m = np.asarray(img)[..., 3] > 0
        c = 48
        if m[c, c]:
            kind = "land"
        else:
            ys, xs = np.nonzero(m)
            km_px = 12.0 / 96
            near = len(ys) and np.sqrt(((ys - c) * km_px) ** 2 + ((xs - c) * km_px) ** 2).min() <= coast_km
            kind = "coast" if near else "sea"
        cache[key] = kind
        return kind

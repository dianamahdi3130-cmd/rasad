"""Minimal WMS client for EUMETView (EUMETSAT) plus helpers to read fire symbols."""
from __future__ import annotations

import datetime as dt
import io
import re
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager

import numpy as np
import requests
from PIL import Image

from .util import floor_time, iso, log, parse_time, utcnow


class WMS:
    def __init__(self, url, session, timeout=20):
        self.url, self.s, self.timeout = url, session, timeout
        self._caps = None
        self._deadline = None
        self._request_limit = None

    @contextmanager
    def budget(self, seconds, timeout_s=20):
        """Share a request deadline across a phase and its download threads."""
        previous = self._deadline, self._request_limit
        deadline = time.monotonic() + max(0, float(seconds))
        self._deadline = min(deadline, self._deadline) if self._deadline is not None else deadline
        self._request_limit = min(float(timeout_s), self._request_limit or float(timeout_s))
        try:
            yield self
        finally:
            self._deadline, self._request_limit = previous

    def _timeout(self, requested=None):
        timeout = min(float(requested or self.timeout), self._request_limit or float(self.timeout))
        if self._deadline is not None:
            timeout = min(timeout, self._deadline - time.monotonic())
        return timeout if timeout > 0 else None

    def _base(self, layer, bbox, W, H, t, style):
        p = {"SERVICE": "WMS", "VERSION": "1.1.1", "SRS": "EPSG:4326", "LAYERS": layer,
             "STYLES": style or "", "BBOX": ",".join(f"{v:.5f}" for v in bbox),
             "WIDTH": str(int(W)), "HEIGHT": str(int(H)),
             "EXCEPTIONS": "application/vnd.ogc.se_xml"}
        if t is not None:
            p["TIME"] = t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return p

    def getmap(self, layer, bbox, W, H, t=None, style="", quiet=False, timeout=None):
        """(RGBA image, time actually served) or (None, None)."""
        p = self._base(layer, bbox, W, H, t, style)
        p.update({"REQUEST": "GetMap", "FORMAT": "image/png", "TRANSPARENT": "TRUE"})
        timeout = self._timeout(timeout)
        if timeout is None:
            return None, None
        try:
            r = self.s.get(self.url, params=p, timeout=timeout)
        except requests.RequestException as e:
            if not quiet:
                log.warning("WMS %s: network error: %s", layer, e)
            return None, None
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200 or not ctype.startswith("image/"):
            if not quiet:
                body = re.sub(r"\s+", " ", r.text[:300]) if ("xml" in ctype or "text" in ctype) else ""
                log.warning("WMS %s @%s: HTTP %s %s %s", layer, iso(t) if t else "default",
                            r.status_code, ctype, body)
            return None, None
        try:
            img = Image.open(io.BytesIO(r.content))
            img.load()
        except Exception as e:  # noqa: BLE001
            if not quiet:
                log.warning("WMS %s: unreadable image: %s", layer, e)
            return None, None
        actual = None
        m = re.search(r"time=(\d{4}-\d\d-\d\dT[\d:.]+Z?)", r.headers.get("Warning", ""))
        if m:
            try:
                actual = parse_time(m.group(1))
            except ValueError:
                actual = None
        return img.convert("RGBA"), actual

    def feature_info(self, layer, bbox, W, H, col, row, t=None):
        p = self._base(layer, bbox, W, H, t, "")
        p.update({"REQUEST": "GetFeatureInfo", "QUERY_LAYERS": layer,
                  "X": str(int(round(col))), "Y": str(int(round(row))),
                  "INFO_FORMAT": "application/json", "FEATURE_COUNT": "5"})
        timeout = self._timeout()
        if timeout is None:
            return None
        try:
            r = self.s.get(self.url, params=p, timeout=timeout)
            if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                return None
            feats = r.json().get("features") or []
        except (requests.RequestException, ValueError):
            return None
        return (feats[0].get("properties") or {}) if feats else None

    def time_end(self, layer):
        """Last time advertised for `layer` in GetCapabilities (or None)."""
        if self._caps is None:
            timeout = self._timeout()
            if timeout is None:
                return None
            self._caps = False
            try:
                r = self.s.get(self.url, params={"SERVICE": "WMS", "VERSION": "1.3.0",
                                                 "REQUEST": "GetCapabilities"}, timeout=timeout)
                if r.status_code == 200:
                    self._caps = ET.fromstring(r.content)
            except (requests.RequestException, ET.ParseError) as e:
                log.warning("GetCapabilities failed: %s", e)
        if self._caps is False:
            return None
        for el in self._caps.iter():
            if not el.tag.endswith("Layer"):
                continue
            name, extent = None, None
            for ch in el:
                tag = ch.tag.split("}")[-1]
                if tag == "Name":
                    name = (ch.text or "").strip()
                elif tag in ("Dimension", "Extent") and ch.attrib.get("name", "").lower() == "time":
                    extent = (ch.text or "").strip()
            if name == layer and extent:
                last = extent.split(",")[-1].strip()
                last = last.split("/")[1] if "/" in last else last
                try:
                    return parse_time(last)
                except ValueError:
                    return None
        return None


def coverage(img):
    return float((np.asarray(img)[..., 3] > 0).mean())


def components(mask, limit=60000):
    """8-connected components of a boolean mask -> list of (N,2) [row, col] arrays."""
    if mask.sum() > limit:
        return []
    H, W = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        stack, pts = [(y0, x0)], []
        while stack:
            y, x = stack.pop()
            pts.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        comps.append(np.array(pts))
    return comps


def served(wms, layer, t, pbox=None, timeout=20):
    """Is the picture of time t out at the source? One tiny picture tells: the server answers a
    time it does not have with its nearest one (and says which). True / False / None (no answer)."""
    pbox = pbox or [56.2, 26.2, 56.4, 26.4]
    img, actual = wms.getmap(layer, pbox, 8, 8, t=t, quiet=True, timeout=timeout)
    if img is None:
        return None
    return actual is None or abs((actual - t).total_seconds()) <= 120


class Availability:
    """Which picture times the source has, asked once per run. A time it did not have is asked
    again only after a while (an outage at the source can leave holes that are never filled)."""

    def __init__(self, wms, state, now, retry_min=10, fresh_min=40):
        self.wms, self.now = wms, now
        self.missing = state.setdefault("missing_pictures", {})
        self.retry = dt.timedelta(minutes=retry_min)
        self.fresh = dt.timedelta(minutes=fresh_min)
        self.known, self.asked = {}, 0
        for k in [k for k, v in self.missing.items() if now - parse_time(v) > dt.timedelta(hours=8)]:
            del self.missing[k]

    def has(self, layer, t, pbox=None):
        key = f"{layer}|{iso(t)}"
        if key in self.known:
            return self.known[key]
        last = self.missing.get(key)
        # a recent picture may just be late: ask every run; an old hole only now and then
        if last and self.now - t > self.fresh and self.now - parse_time(last) < self.retry:
            self.known[key] = False
            return False
        self.asked += 1
        ok = served(self.wms, layer, t, pbox)
        if ok is False:
            self.missing[key] = iso(self.now)
        elif ok:
            self.missing.pop(key, None)
        self.known[key] = ok
        return ok

    def holes(self, layer, since):
        """Times of `layer` after `since` that the source did not have when asked."""
        return sorted(k.rsplit("|", 1)[1] for k in self.missing
                      if k.startswith(layer + "|") and parse_time(k.rsplit("|", 1)[1]) >= since)


def latest_time(wms, state, layer, cadence, pbox, max_steps=2):
    """Find a real picture time with a small number of adequately timed probes.

    A valid tiny clock picture can take about fifteen seconds at this source.
    The phase budget bounds an outage without rejecting that response at eight
    seconds, and an enclosing collection budget still takes precedence.
    """
    from contextlib import nullcontext
    method = getattr(type(wms), "budget", None)
    context = wms.budget(22, timeout_s=20) if callable(method) else nullcontext()
    with context:
        return _latest_time(wms, state, layer, cadence, pbox, max_steps)


def _latest_time(wms, state, layer, cadence, pbox, max_steps):
    known = state.setdefault("latest", {}).get(layer)
    known_t = parse_time(known) if known else None
    cands, t = [], floor_time(utcnow(), cadence)
    while len(cands) < max_steps and (known_t is None or t > known_t):
        cands.append(t)
        t -= dt.timedelta(minutes=cadence)
    if known_t is not None:
        cands.append(known_t)
    for t in cands:
        if isinstance(wms, WMS) and wms._timeout() is None:
            break
        img, actual = wms.getmap(layer, pbox, 24, 24, t=t, quiet=True)
        if img is not None and coverage(img) > 0.5:
            found = actual or t
            state["latest"][layer] = iso(found)
            return found
    # GetMap usually tells us its actual time directly. Do not let a slow
    # capabilities document consume a cold start before the first data probe.
    if known_t is None and (not isinstance(wms, WMS) or wms._timeout() is not None):
        end = wms.time_end(layer)
        if end is not None:
            t = floor_time(end, cadence)
            img, actual = wms.getmap(layer, pbox, 24, 24, t=t, quiet=True)
            if img is not None and coverage(img) > 0.5:
                found = actual or t
                state["latest"][layer] = iso(found)
                return found
    return known_t

"""Telegram delivery (works in the background: alerts reach the phone even when the app is closed)."""
from __future__ import annotations

import re
from pathlib import Path

import requests

from .util import log

KINDS = {".mp4": ("sendVideo", "video"), ".gif": ("sendAnimation", "animation"),
         ".png": ("sendPhoto", "photo"), ".jpg": ("sendPhoto", "photo")}


def telegram_ready(cfg):
    T = cfg["telegram"]
    return bool(str(T.get("bot_token") or "").strip() and str(T.get("chat_id") or "").strip())


def tg_send(cfg, session, text, media=None):
    T = cfg["telegram"]
    base = f"{str(T.get('api_base') or 'https://api.telegram.org').rstrip('/')}/bot{str(T['bot_token']).strip()}"
    chat = str(T["chat_id"]).strip()
    try:
        if media and Path(media).exists() and Path(media).suffix.lower() in KINDS:
            method, field = KINDS[Path(media).suffix.lower()]
            caption = text if len(text) <= 1024 else text.split("\n")[0]
            with open(media, "rb") as f:
                r = session.post(f"{base}/{method}", data={"chat_id": chat, "caption": caption,
                                                            "parse_mode": "HTML"},
                                 files={field: (Path(media).name, f)}, timeout=180)
            ok = r.status_code == 200 and r.json().get("ok")
            if ok and len(text) > 1024:
                return tg_send(cfg, session, text, None)
        else:
            r = session.post(f"{base}/sendMessage", data={"chat_id": chat, "text": text[:4096],
                                                          "parse_mode": "HTML",
                                                          "disable_web_page_preview": "true"}, timeout=60)
            ok = r.status_code == 200 and r.json().get("ok")
        if not ok:
            log.warning("Telegram refused the message: %s", r.text[:300])
        return bool(ok)
    except (requests.RequestException, ValueError) as e:
        log.warning("Telegram error: %s", e)
        return False


def deliver(cfg, session, text, media, send):
    if send and telegram_ready(cfg):
        return tg_send(cfg, session, text, media)
    print("\n" + re.sub(r"<[^>]+>", "", text) + (f"\n[clip] {media}" if media else "") + "\n", flush=True)
    return True

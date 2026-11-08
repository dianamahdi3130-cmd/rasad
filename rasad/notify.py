"""Telegram delivery (works in the background: alerts reach the phone even when the app is closed)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from .util import log

KINDS = {".mp4": ("sendVideo", "video"), ".gif": ("sendAnimation", "animation"),
         ".png": ("sendPhoto", "photo"), ".jpg": ("sendPhoto", "photo")}


def telegram_ready(cfg):
    T = cfg["telegram"]
    return bool(str(T.get("bot_token") or "").strip() and str(T.get("chat_id") or "").strip())


def tg_send(cfg, session, text, media=None, reply_to=None):
    """Send a post (with its clip when there is one). Returns the post's message id, or None.
    reply_to: the id of an earlier post this one answers (updates thread under the first alert)."""
    T = cfg["telegram"]
    base = f"{str(T.get('api_base') or 'https://api.telegram.org').rstrip('/')}/bot{str(T['bot_token']).strip()}"
    chat = str(T["chat_id"]).strip()
    extra = {}
    if reply_to:
        extra["reply_parameters"] = json.dumps({"message_id": int(reply_to), "allow_sending_without_reply": True})
    try:
        if media and Path(media).exists() and Path(media).suffix.lower() in KINDS:
            method, field = KINDS[Path(media).suffix.lower()]
            caption = text if len(text) <= 1024 else text.split("\n")[0]
            data = {"chat_id": chat, "caption": caption, "parse_mode": "HTML", **extra}
            if method == "sendVideo":
                data["supports_streaming"] = "true"
            with open(media, "rb") as f:
                r = session.post(f"{base}/{method}", data=data, files={field: (Path(media).name, f)},
                                 timeout=float(T.get("media_timeout_s", 180)))
            ok = r.status_code == 200 and r.json().get("ok")
            if ok and len(text) > 1024:
                mid = (r.json().get("result") or {}).get("message_id")
                tg_send(cfg, session, text, None, reply_to=mid)
                return mid or True
        else:
            r = session.post(f"{base}/sendMessage", data={"chat_id": chat, "text": text[:4096],
                                                          "parse_mode": "HTML",
                                                          "link_preview_options": json.dumps({"is_disabled": True}),
                                                          "disable_web_page_preview": "true", **extra},
                             timeout=float(T.get("text_timeout_s", 60)))
            ok = r.status_code == 200 and r.json().get("ok")
        if not ok:
            log.warning("Telegram refused the message: %s", r.text[:300])
            return None
        res = r.json().get("result")
        return (res.get("message_id") if isinstance(res, dict) else None) or True
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("Telegram request failed (%s)", type(e).__name__)
        return None


def deliver(cfg, session, text, media, send, reply_to=None):
    """Post to the channel. Returns the post's id (or True when only printed), None on failure."""
    if send and telegram_ready(cfg):
        return tg_send(cfg, session, text, media, reply_to)
    print("\n" + re.sub(r"<[^>]+>", "", text) + (f"\n[clip] {media}" if media else "")
          + (f"\n[reply to {reply_to}]" if reply_to else "") + "\n", flush=True)
    return True

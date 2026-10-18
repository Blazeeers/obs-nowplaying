#!/usr/bin/env python3
"""
Now Playing — локальный сервер для OBS-виджета «что сейчас играет».

Источники данных (в порядке приоритета):
  1. Windows SMTC — приложение Яндекс Музыки (и любой плеер с медиа-интеграцией):
     название, исполнитель, альбом, обложка, позиция.
  2. Chrome DevTools Protocol — веб-плеер music.yandex в Chrome/Edge/Яндекс Браузере,
     если браузер запущен с --remote-debugging-port=9222.
  3. Заголовок окна плеера — грубый резерв («Исполнитель — Трек»).

Страницы:
  http://127.0.0.1:8770/             — виджет (для Browser Source)
  http://127.0.0.1:8770/configurator — настройка вида + ссылка для OBS

API:
  /api/state  — JSON
  /api/art    — обложка (PNG/JPEG)
  /health     — проверка живости

Запуск:
  python np-server.py [--port 8770] [--no-cdp] [--debug]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
WIDGET = os.path.join(HERE, "widget.html")
CONFIG = os.path.join(HERE, "configurator.html")

STATE = {
    "playing": False, "paused": True,
    "title": "", "artist": "", "album": "",
    "pos": 0.0, "dur": 0.0,
    "art": 0, "app": "", "src": "", "updated": 0.0,
}
ART = {"data": b"", "ver": 0}
LOCK = threading.Lock()
DEBUG = False
USE_CDP = True


def log(*a):
    if DEBUG:
        print("[np]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- CDP (веб-плеер)
CDP_JS = r"""
(async () => {
  const out = {src:"", title:"", artist:"", album:"", art:"", artData:"", pos:0, dur:0, playing:null};
  try {
    const ms = navigator.mediaSession && navigator.mediaSession.metadata;
    if (ms && (ms.title || ms.artist)) {
      out.src = "mediaSession";
      out.title = ms.title || ""; out.artist = ms.artist || ""; out.album = ms.album || "";
      const aw = ms.artwork || [];
      out.art = aw.length ? (aw[aw.length - 1].src || "") : "";
      if (navigator.mediaSession.playbackState)
        out.playing = navigator.mediaSession.playbackState === "playing";
    }
  } catch (e) {}
  if (!out.title) {
    const q = s => document.querySelector(s);
    const txt = e => (e ? (e.textContent || "").trim().replace(/\s+/g, " ") : "");
    const t = txt(q('[class*="player-controls__track-info"] [class*="track__title"]'))
           || txt(q('[class*="PlayerBar"] [class*="title"]'))
           || txt(q('[class*="track__title"]'));
    const a = txt(q('[class*="player-controls__track-info"] [class*="track__artists"]'))
           || txt(q('[class*="PlayerBar"] [class*="artist"]'))
           || txt(q('[class*="track__artists"]'));
    if (t) { out.src = "dom"; out.title = t; out.artist = a; }
    const img = q('[class*="player-controls"] img') || q('[class*="PlayerBar"] img');
    if (img && !out.art) out.art = img.currentSrc || img.src || "";
  }
  const au = document.querySelector("audio");
  if (au) {
    out.pos = au.currentTime || 0; out.dur = au.duration || 0;
    if (out.playing === null) out.playing = !au.paused;
  }
  if (out.art) {
    try {
      const r = await fetch(out.art);
      const b = await r.blob();
      out.artData = await new Promise(res => {
        const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(b);
      });
    } catch (e) {}
  }
  out.dt = document.title;
  return JSON.stringify(out);
})()
"""

_ws = {"url": None, "bad": 0.0}


def _cdp_ports():
    raw = os.environ.get("NP_CDP_PORTS", "9222,9223")
    out = []
    for p in raw.split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out


def cdp_find():
    for port in _cdp_ports():
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=0.7
            ) as r:
                targets = json.load(r)
        except Exception:
            continue
        for t in targets:
            url = t.get("url") or ""
            if t.get("type") == "page" and "music.yandex" in url:
                return t.get("webSocketDebuggerUrl")
    return None


def cdp_eval(expr):
    try:
        import websocket  # websocket-client
    except ImportError:
        return None
    url = _ws["url"]
    if not url or time.time() - _ws["bad"] < 5:
        url = cdp_find()
        _ws["url"] = url
    if not url:
        return None
    try:
        ws = websocket.create_connection(url, timeout=2.0, skip_utf8_validation=True)
    except Exception:
        _ws["url"] = None
        _ws["bad"] = time.time()
        return None
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
        }))
        for _ in range(60):
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg.get("result", {}).get("result", {}).get("value")
    except Exception:
        _ws["url"] = None
        _ws["bad"] = time.time()
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return None


def cdp_once():
    raw = cdp_eval(CDP_JS)
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    title = (d.get("title") or "").strip()
    if not title:
        return None
    art = b""
    ad = d.get("artData") or ""
    if ad.startswith("data:"):
        try:
            art = base64.b64decode(ad.split(",", 1)[1])
        except Exception:
            art = b""
    return {
        "title": title,
        "artist": (d.get("artist") or "").strip(),
        "album": (d.get("album") or "").strip(),
        "pos": round(float(d.get("pos") or 0), 2),
        "dur": round(float(d.get("dur") or 0), 2),
        "playing": bool(d.get("playing")),
        "app": "browser",
        "src": "cdp:" + (d.get("src") or "?"),
        "art": art,
    }


# ---------------------------------------------------------------- SMTC (Windows)
async def _thumb_bytes(thumb):
    try:
        stream = await thumb.open_read_async()
        size = stream.size
        if not size:
            return b""
        from winsdk.windows.storage.streams import DataReader
        reader = DataReader(stream)
        await reader.load_async(size)
        buf = bytearray(size)
        reader.read_bytes(buf)
        reader.detach_stream()
        return bytes(buf)
    except Exception:
        return b""


async def smtc_once(manager):
    session = manager.get_current_session() if manager else None
    if session is None:
        return None

    props = None
    try:
        props = await session.try_get_media_properties_async()
    except Exception:
        pass

    status = 4
    try:
        status = int(session.get_playback_info().playback_status)
    except Exception:
        pass

    pos = dur = 0.0
    try:
        tl = session.get_timeline_properties()
        pos = tl.position.total_seconds()
        dur = tl.end_time.total_seconds()
        if status == 4 and tl.last_updated_time:
            delta = (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
            if 0 < delta < 5:
                pos += delta
    except Exception:
        pass

    title = (getattr(props, "title", "") or "").strip()
    if not title:
        return None

    art = b""
    if props is not None and props.thumbnail is not None:
        art = await _thumb_bytes(props.thumbnail)

    return {
        "title": title,
        "artist": (getattr(props, "artist", "") or "").strip(),
        "album": (getattr(props, "album_title", "") or "").strip(),
        "pos": round(pos, 2),
        "dur": round(dur, 2),
        "playing": status == 4,
        "app": session.source_app_user_model_id or "",
        "src": "smtc",
        "art": art,
    }


# ---------------------------------------------------------------- заголовок окна
TARGET_PIDS: set[int] = set()


def pids_of(*names):
    if os.name != "nt":
        return set()
    try:
        cmd = ("Get-Process | Where-Object { $_.ProcessName -in @(%s) } | "
               "ForEach-Object { $_.Id }" % ",".join("'%s'" % n for n in names))
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=4,
        ).stdout
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        return set()


def title_once():
    if os.name != "nt" or not TARGET_PIDS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        found: list[str] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in TARGET_PIDS:
                n = u32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u32.GetWindowTextW(hwnd, buf, n + 1)
                    t = buf.value.strip()
                    if t:
                        found.append(t)
            return True

        u32.EnumWindows(cb, 0)
    except Exception:
        return None

    bad = {"MSCTFIME UI", "Default IME", "Яндекс Музыка", "Yandex Music"}
    found = [t for t in found if t not in bad]
    if not found:
        return None
    title = max(found, key=len)
    for sep in (" — ", " – ", " - "):
        if sep in title:
            a, b = (x.strip() for x in title.split(sep, 1))
            if a and b:
                return {
                    "title": b, "artist": a, "album": "",
                    "pos": 0.0, "dur": 0.0, "playing": True,
                    "app": "player", "src": "window", "art": b"",
                }
    return None


# ---------------------------------------------------------------- общий цикл
def publish(data, prev_title):
    with LOCK:
        STATE.update(
            playing=data["playing"], paused=not data["playing"],
            title=data["title"], artist=data["artist"], album=data["album"],
            pos=data["pos"], dur=data["dur"], app=data["app"],
            src=data["src"], updated=time.time(),
        )
    art = data.get("art") or b""
    if data["title"] != prev_title or not ART["data"]:
        if art:
            ver = int(hashlib.md5(art).hexdigest()[:8], 16)
            with LOCK:
                ART["data"] = art
                ART["ver"] = ver
                STATE["art"] = ver
        else:
            with LOCK:
                ART["data"] = b""
                ART["ver"] = 0
                STATE["art"] = 0
    log("publish", data["src"], data["title"])
    return data["title"]


def go_idle():
    with LOCK:
        if STATE["title"]:
            log("idle")
        STATE.update(playing=False, paused=True, title="", artist="", album="",
                     pos=0.0, dur=0.0, app="", src="", art=0)
        ART["data"] = b""
        ART["ver"] = 0


def poller():
    manager = None
    has_smtc = False
    if os.name == "nt":
        try:
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Manager,
            )
            has_smtc = True
        except Exception as e:
            log("SMTC недоступен:", e)

    loop = None
    if has_smtc:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    prev = ""
    tick = 0

    while True:
        data = None

        if has_smtc:
            try:
                if manager is None:
                    manager = loop.run_until_complete(Manager.request_async())
                data = loop.run_until_complete(smtc_once(manager))
            except Exception as e:
                log("smtc error:", e)
                manager = None
                time.sleep(1.0)

        tick += 1
        if data is None and USE_CDP and tick % 2 == 0:
            try:
                data = cdp_once()
            except Exception as e:
                log("cdp error:", e)

        if data is None:
            if tick % 20 == 1:
                TARGET_PIDS.clear()
                TARGET_PIDS.update(pids_of("Y.Music", "yandexmusic", "browser",
                                           "chrome", "msedge", "opera", "brave"))
            data = title_once()

        if data is None:
            prev = ""
            go_idle()
        else:
            prev = publish(data, prev)

        time.sleep(0.5)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "NowPlaying/1.0"

    def log_message(self, *a):
        if DEBUG:
            super().log_message(*a)

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)
        except FileNotFoundError:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"

        if path in ("/", "/index.html", "/widget.html"):
            self._file(WIDGET, "text/html; charset=utf-8")
        elif path in ("/configurator", "/configurator.html", "/config"):
            self._file(CONFIG, "text/html; charset=utf-8")
        elif path == "/api/state":
            with LOCK:
                payload = dict(STATE)
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif path == "/api/art":
            with LOCK:
                data = ART["data"]
            if not data:
                self._send(404, b"", "image/png")
            else:
                ctype = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
                self._send(200, data, ctype)
        elif path == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")


# ---------------------------------------------------------------- main
def main():
    global DEBUG, USE_CDP

    ap = argparse.ArgumentParser(description="Now Playing server for OBS")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NP_PORT", 8770)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-cdp", action="store_true", help="не читать браузер через CDP")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    DEBUG = args.debug
    USE_CDP = not args.no_cdp

    threading.Thread(target=poller, daemon=True).start()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Now Playing: http://{args.host}:{args.port}/")
    print(f"Настройка:   http://{args.host}:{args.port}/configurator")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено")


if __name__ == "__main__":
    main()

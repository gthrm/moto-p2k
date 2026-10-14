#!/usr/bin/env python3
"""Moto P2K: a window for transferring files to/from a Motorola RAZR V3r (and other P2K phones) over USB.

Starts a local server on 127.0.0.1 and opens the UI in the browser.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
import io
import json
import mimetypes
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usb.core  # noqa: E402

import p2k  # noqa: E402

PORT = 8765
HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p2k_ui.html")
ALLOWED_HOSTS = {"127.0.0.1:%d" % PORT, "localhost:%d" % PORT}
MAX_UPLOAD = 20 * 1024 * 1024  # the phone has a few MB of user memory at most
LOST = "The phone stopped responding. Unplug the cable and plug it back in."

mimetypes.add_type("audio/amr", ".amr")
mimetypes.add_type("audio/midi", ".mid")
mimetypes.add_type("video/3gpp", ".3gp")

lock = threading.Lock()
state = {"phone": None, "files": None, "space": None, "last_ping": time.time()}


class AppError(Exception):
    def __init__(self, msg, code=500):
        super().__init__(msg)
        self.code = code


def drop_phone():
    if state["phone"] is not None:
        state["phone"].release()
    state["phone"] = None
    state["files"] = None
    state["space"] = None


def phone_status():
    if p2k.find_dev(p2k.PID_P2K) is not None:
        return "p2k"
    # the phone is gone (or back in modem mode): forget the stale connection
    if state["phone"] is not None and lock.acquire(blocking=False):
        try:
            drop_phone()
        finally:
            lock.release()
    if p2k.find_dev(p2k.PID_AT) is not None:
        return "at"
    return "none"


def with_phone(fn):
    """Run fn(phone) under the lock; drop the connection only if USB itself fails."""
    with lock:
        try:
            if state["phone"] is None:
                state["phone"] = p2k.Phone()
            return fn(state["phone"])
        except (usb.core.USBError, p2k.ProtocolLost):
            drop_phone()
            raise AppError(LOST, 503)
        except p2k.P2KError as e:
            raise AppError(str(e), 502)


def file_list(refresh=False):
    if refresh or state["files"] is None:
        state["files"] = with_phone(lambda ph: ph.list_files())
    return state["files"]


def free_space(refresh=False):
    """{'/a/': bytes, '/c/': bytes, ...} for every volume the phone reports."""
    if refresh or state["space"] is None:
        state["space"] = with_phone(lambda ph: {v + "/": ph.free_space(v) for v in ph.volumes()})
    return state["space"]


def file_info(path):
    for name, size, attr in file_list():
        if name == path:
            return name, size, attr
    raise AppError("No such file on the phone: " + path, 404)


def read_file(path):
    name, size, attr = file_info(path)
    return with_phone(lambda ph: ph.read_all(name, size, attr))


TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dz",
}


def _latin(s):
    s = "".join(
        (TRANSLIT[c.lower()].capitalize() if c.isupper() else TRANSLIT[c]) if c.lower() in TRANSLIT else c
        for c in s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9 ._~()\-]", "_", s)


def safe_name(name):
    """The phone only accepts Latin characters in file names."""
    base, ext = os.path.splitext(name)
    return (_latin(base).strip()[:60] or "file") + _latin(ext)[:8]


def unique_path(folder, name, taken):
    base, ext = os.path.splitext(name)
    path, i = folder + name, 1
    while path.lower() in taken:
        path = "%s%s_%d%s" % (folder, base, i, ext)
        i += 1
    return path


def write_file(folder, name, data):
    if not folder.endswith("/"):
        folder += "/"
    files = file_list()
    path = unique_path(folder, safe_name(name), {n.lower() for n, _, _ in files})
    free = free_space(refresh=True).get(path[:3])
    if free is not None and len(data) > free:
        raise AppError("Not enough space on the phone: %s needs %s, only %s free"
                       % (name, p2k.human(len(data)), p2k.human(free)), 507)
    try:
        with_phone(lambda ph: ph.write_all(path, data))
    finally:
        state["space"] = None
    if state["files"] is not None:
        state["files"].append((path, len(data), 0))
    return path


def delete_files(paths):
    def go(ph):
        for p in paths:
            ph.delete(p)
            if state["files"] is not None:
                state["files"] = [f for f in state["files"] if f[0] != p]
            time.sleep(0.3)

    try:
        with_phone(go)
    finally:
        state["space"] = None


def zip_files(paths):
    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p in paths:
            name = os.path.basename(p)
            base, ext = os.path.splitext(name)
            i = 1
            while name.lower() in used:
                name = "%s_%d%s" % (base, i, ext)
                i += 1
            used.add(name.lower())
            z.writestr(name, read_file(p))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, ctype, filename=None, inline=False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            disp = "inline" if inline else "attachment"
            self.send_header("Content-Disposition",
                             "%s; filename*=UTF-8''%s" % (disp, urllib.parse.quote(filename)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise AppError("File is too large for the phone", 413)
        return self.rfile.read(n) if n else b""

    def _trusted(self, post):
        # Only our own page may talk to us: blocks DNS rebinding (Host) and
        # cross-site requests (custom header forces a CORS preflight we never allow).
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return False
        if not post:
            return True
        origin = self.headers.get("Origin")
        if origin is not None and origin.split("//", 1)[-1] not in ALLOWED_HOSTS:
            return False
        return self.headers.get("X-P2K") == "1"

    def _handle(self, post):
        if not self._trusted(post):
            self.send_error(403)
            return
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            route = (self.command, u.path)
            if route == ("GET", "/"):
                with open(HTML, "rb") as f:
                    self._bytes(f.read(), "text/html; charset=utf-8")
            elif route == ("GET", "/api/status"):
                self._json({"status": phone_status()})
            elif route == ("GET", "/api/ping"):
                state["last_ping"] = time.time()
                self._json({"ok": True})
            elif route == ("GET", "/api/list"):
                files = file_list(refresh=q.get("refresh") == "1")
                self._json({"files": [{"path": n, "size": s, "attr": a} for n, s, a in files]})
            elif route == ("GET", "/api/space"):
                self._json({"free": free_space(refresh=q.get("refresh") == "1")})
            elif route == ("GET", "/api/file"):
                path = q["path"]
                ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
                self._bytes(read_file(path), ctype, os.path.basename(path), inline=q.get("inline") == "1")
            elif route == ("POST", "/api/upload"):
                self._json({"path": write_file(q["dir"], q["name"], self._body())})
            elif route == ("POST", "/api/delete"):
                delete_files(json.loads(self._body())["paths"])
                self._json({"ok": True})
            elif route == ("POST", "/api/zip"):
                self._bytes(zip_files(json.loads(self._body())["paths"]), "application/zip", "V3r_files.zip")
            elif route == ("POST", "/api/quit"):
                self._json({"ok": True})
                threading.Thread(target=shutdown, daemon=True).start()
            else:
                self.send_error(404)
        except AppError as e:
            self._json({"error": str(e)}, e.code)
        except (KeyError, ValueError) as e:
            self._json({"error": "Bad request: %s" % e}, 400)

    def do_GET(self):
        self._handle(post=False)

    def do_POST(self):
        self._handle(post=True)


server = None


def shutdown():
    time.sleep(0.3)
    server.shutdown()


def watchdog():
    # Stop the server once the browser tab has been gone for 10 minutes.
    # A big gap between our own ticks means the Mac was asleep: don't count that time.
    last_tick = time.time()
    while True:
        time.sleep(15)
        now = time.time()
        if now - last_tick > 60:
            state["last_ping"] = now
        last_tick = now
        if now - state["last_ping"] > 600 and not lock.locked():
            shutdown()
            return


def main():
    global server
    url = "http://127.0.0.1:%d/" % PORT
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        webbrowser.open(url)  # already running: just open the window
        return
    threading.Thread(target=watchdog, daemon=True).start()
    webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()

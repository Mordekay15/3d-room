"""The local web app: serves the pages, takes uploads, runs scan jobs.

Plain Python standard library - no web framework to install.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, config, scene

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
    ".ico": "image/x-icon", ".wasm": "application/wasm", ".ply": "application/octet-stream",
    ".txt": "text/plain; charset=utf-8", ".map": "application/json",
}
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mts"}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class Jobs:
    """Runs scan pipelines one at a time (two at once would crawl on a laptop)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, object] = {}
        self.queue: list[str] = []
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, scene_id: str):
        with self.lock:
            if scene_id in self.running or scene_id in self.queue:
                return
            self.queue.append(scene_id)
        config.write_json(config.SCENES_DIR / scene_id / "status.json",
                          {"state": "queued", "message": "Waiting for the current scan to finish",
                           "updated": time.time()})

    def _loop(self):
        while True:
            time.sleep(0.5)
            with self.lock:
                for sid, proc in list(self.running.items()):
                    if proc.poll() is not None:
                        del self.running[sid]
                        self._close_log(sid)
                        self._check_crash(sid, proc.returncode)
                if self.running or not self.queue:
                    continue
                sid = self.queue.pop(0)
                scene_dir = config.SCENES_DIR / sid
                if not scene_dir.is_dir():
                    continue
                out = open(scene_dir / "log.txt", "a", encoding="utf-8")
                kwargs = {"creationflags": NO_WINDOW} if sys.platform == "win32" else \
                         {"start_new_session": True}
                self.logs[sid] = out
                self.running[sid] = subprocess.Popen(
                    [sys.executable, "-m", "roomwalk.pipeline", str(scene_dir)],
                    cwd=str(config.ROOT), stdout=out, stderr=subprocess.STDOUT, **kwargs)

    def _close_log(self, sid: str):
        handle = self.logs.pop(sid, None)
        if handle:
            try:
                handle.close()  # an open handle would stop Windows deleting the scan folder
            except OSError:
                pass

    @staticmethod
    def _check_crash(sid: str, code: int):
        """If the pipeline died without saying why (a hard crash), say so."""
        path = config.SCENES_DIR / sid / "status.json"
        status = config.read_json(path, {}) or {}
        if status.get("state") == "running":
            stage = status.get("stage")
            if stage and "stages" in status:
                status["stages"][stage]["state"] = "failed"
            status.update(state="failed", message="Stopped",
                          error="This step stopped unexpectedly (code %s). Press Retry to carry on "
                                "from here. If it keeps happening, try the Quick quality, and see "
                                "log.txt in the scan's folder for details." % code)
            config.write_json(path, status)

    def cancel(self, sid: str):
        with self.lock:
            if sid in self.queue:
                self.queue.remove(sid)
            proc = self.running.pop(sid, None)
        if proc and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, creationflags=NO_WINDOW)
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        with self.lock:
            self._close_log(sid)
        path = config.SCENES_DIR / sid / "status.json"
        status = config.read_json(path, {}) or {}
        if status.get("state") in ("running", "queued"):
            status.update(state="cancelled", message="Stopped by you", error=None)
            config.write_json(path, status)

    def is_active(self, sid: str) -> bool:
        with self.lock:
            return sid in self.running or sid in self.queue


JOBS: Jobs | None = None


def scene_summary(scene_dir: Path) -> dict | None:
    meta = config.read_json(scene_dir / "meta.json")
    if meta is None:
        return None
    status = config.read_json(scene_dir / "status.json", {}) or {}
    if status.get("state") in ("running", "queued") and not JOBS.is_active(scene_dir.name):
        # left over from a previous run of the app (closed mid-scan)
        status.update(state="interrupted", message="Stopped when the app was closed", error=None)
    view = config.read_json(scene_dir / "scene.json")
    return {
        "id": scene_dir.name,
        "name": meta.get("name", scene_dir.name),
        "kind": meta.get("kind", "scan"),
        "preset": meta.get("preset"),
        "created": meta.get("created", 0),
        "status": status,
        "viewable": view is not None,
        "quality": view.get("quality") if view else None,
        "splats": view["splat"]["count"] if view else 0,
        "thumb": (scene_dir / "thumb.jpg").exists(),
        "has_video": any(scene_dir.glob("video.*")),
    }


def app_state() -> dict:
    from . import trainer

    scenes = []
    if config.SCENES_DIR.is_dir():
        for d in config.SCENES_DIR.iterdir():
            if d.is_dir():
                s = scene_summary(d)
                if s:
                    scenes.append(s)
    scenes.sort(key=lambda s: (s["kind"] == "demo", -s["created"]))
    brush = trainer.find_brush()
    return {
        "version": __version__,
        "scenes": scenes,
        "presets": [{"id": k, **{f: v[f] for f in ("label", "blurb", "frames", "steps")}}
                    for k, v in config.PRESETS.items()],
        "default_preset": config.DEFAULT_PRESET,
        "stages": [{"id": k, "label": label} for k, label in config.STAGES],
        "engine_installed": brush is not None,
        "camera_height": config.DEFAULT_CAMERA_HEIGHT,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Roomwalk/" + __version__

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ---- helpers -------------------------------------------------------------
    def send_json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", MIME[".json"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, code, message):
        self.send_json({"error": message}, code)

    def send_file(self, path: Path, cache: bool):
        try:
            size = path.stat().st_size
            f = open(path, "rb")
        except OSError:
            return self.send_error_json(404, "Not found")
        with f:
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(path.suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.end_headers()
            try:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            except (BrokenPipeError, ConnectionError):
                pass

    def read_body_to(self, dest: Path) -> int:
        length = int(self.headers.get("Content-Length") or 0)
        left = length
        with open(dest, "wb") as out:
            while left > 0:
                block = self.rfile.read(min(left, 1 << 20))
                if not block:
                    break
                out.write(block)
                left -= len(block)
        if left:
            dest.unlink(missing_ok=True)
            raise ConnectionError("Upload was cut off")
        return length

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1 << 20:
            raise ValueError("Body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def drain(self):
        length = int(self.headers.get("Content-Length") or 0)
        while length > 0:
            block = self.rfile.read(min(length, 1 << 20))
            if not block:
                break
            length -= len(block)

    def scene_dir(self, sid: str) -> Path | None:
        if not sid or any(c in sid for c in "/\\.") or sid.startswith("-"):
            return None
        d = config.SCENES_DIR / sid
        return d if (d / "meta.json").exists() else None

    def parts(self):
        url = urllib.parse.urlsplit(self.path)
        return [urllib.parse.unquote(p) for p in url.path.split("/") if p], \
            dict(urllib.parse.parse_qsl(url.query))

    # ---- GET -----------------------------------------------------------------
    def do_GET(self):
        parts, query = self.parts()
        if not parts:
            return self.send_file(config.WEB_DIR / "index.html", cache=False)
        if parts[0] == "api":
            if parts[1:] == ["state"]:
                return self.send_json(app_state())
            if len(parts) == 3 and parts[1] == "scenes":
                d = self.scene_dir(parts[2])
                if not d:
                    return self.send_error_json(404, "No such scan")
                return self.send_json(scene_summary(d))
            return self.send_error_json(404, "Not found")
        if parts[0] == "scenes" and len(parts) == 3:
            d = self.scene_dir(parts[1])
            name = parts[2]
            if not d or "/" in name or "\\" in name or name.startswith(".") or \
                    Path(name).suffix.lower() not in (".ply", ".png", ".json", ".jpg"):
                return self.send_error_json(404, "Not found")
            return self.send_file(d / name, cache=False)
        if parts == ["view"]:
            return self.send_file(config.WEB_DIR / "viewer.html", cache=False)
        target = (config.WEB_DIR / Path(*parts)).resolve()
        if config.WEB_DIR.resolve() not in target.parents or not target.is_file():
            return self.send_error_json(404, "Not found")
        return self.send_file(target, cache="vendor" in parts)

    # ---- POST / DELETE -------------------------------------------------------
    def guard(self) -> bool:
        """Only this app's own pages may change things (other websites cannot
        send this header without permission, which is never given)."""
        if self.headers.get("X-Roomwalk") != "1":
            self.drain()
            self.send_error_json(403, "Forbidden")
            return False
        return True

    def do_POST(self):
        if not self.guard():
            return
        parts, query = self.parts()
        try:
            if parts == ["api", "scenes"]:
                return self.create_scan(query)
            if parts == ["api", "import"]:
                return self.import_splat(query)
            if len(parts) == 4 and parts[:2] == ["api", "scenes"]:
                d = self.scene_dir(parts[2])
                if not d:
                    self.drain()
                    return self.send_error_json(404, "No such scan")
                action = parts[3]
                if action == "cancel":
                    self.drain()
                    JOBS.cancel(d.name)
                    return self.send_json(scene_summary(d))
                if action == "retry":
                    self.drain()
                    meta = config.read_json(d / "meta.json", {})
                    if query.get("preset") in config.PRESETS and query["preset"] != meta.get("preset"):
                        # different quality: keep the camera path, redo the 3D build
                        meta["preset"] = query["preset"]
                        config.write_json(d / "meta.json", meta)
                        (d / "trained.json").unlink(missing_ok=True)
                    JOBS.submit(d.name)
                    return self.send_json(scene_summary(d))
                if action == "view":
                    body = self.read_json_body()
                    view = config.read_json(d / "scene.json")
                    if view is None:
                        return self.send_error_json(409, "Scan is not viewable yet")
                    if "eye_height" in body:
                        view["eye_height"] = max(0.3, min(2.5, float(body["eye_height"])))
                    config.write_json(d / "scene.json", view)
                    return self.send_json({"ok": True})
                if action == "reorient":
                    self.drain()
                    meta = config.read_json(d / "meta.json", {})
                    if meta.get("kind") != "import" or query.get("up") not in scene.UP_CHOICES:
                        return self.send_error_json(400, "Only imported scans can be turned")
                    meta["up"] = query["up"]
                    config.write_json(d / "meta.json", meta)
                    view = config.read_json(d / "scene.json", {})
                    scene.publish_import(d, meta, d / view["splat"]["file"])
                    return self.send_json({"ok": True})
            self.drain()
            return self.send_error_json(404, "Not found")
        except Exception as exc:
            return self.send_error_json(500, str(exc))

    def do_DELETE(self):
        if not self.guard():
            return
        parts, _ = self.parts()
        if len(parts) == 3 and parts[:2] == ["api", "scenes"]:
            d = self.scene_dir(parts[2])
            if not d:
                return self.send_error_json(404, "No such scan")
            JOBS.cancel(d.name)
            for attempt in range(10):
                shutil.rmtree(d, ignore_errors=True)
                if not d.exists():
                    break
                time.sleep(0.3)
            if d.exists():
                return self.send_error_json(
                    500, "Some files are still in use. Close the viewer tab and delete again.")
            return self.send_json({"ok": True})
        return self.send_error_json(404, "Not found")

    def create_scan(self, query):
        filename = query.get("filename", "video.mp4")
        ext = Path(filename).suffix.lower()
        if ext not in VIDEO_TYPES:
            self.drain()
            return self.send_error_json(
                400, "That is a %s file. Upload a video (.mp4 or .mov)." % (ext or "unknown"))
        name = (query.get("name") or Path(filename).stem).strip()[:80] or "My room"
        preset = query.get("preset") if query.get("preset") in config.PRESETS else config.DEFAULT_PRESET
        try:
            cam_h = max(0.5, min(2.2, float(query.get("camera_height", config.DEFAULT_CAMERA_HEIGHT))))
        except ValueError:
            cam_h = config.DEFAULT_CAMERA_HEIGHT
        sid = scene.new_scene_id(name)
        d = config.SCENES_DIR / sid
        d.mkdir(parents=True)
        try:
            self.read_body_to(d / ("video" + ext))
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        config.write_json(d / "meta.json", {
            "name": name, "kind": "scan", "preset": preset, "camera_height": cam_h,
            "created": time.time(), "source": filename,
        })
        JOBS.submit(sid)
        return self.send_json(scene_summary(d))

    def import_splat(self, query):
        filename = query.get("filename", "scan.ply")
        if Path(filename).suffix.lower() != ".ply":
            self.drain()
            return self.send_error_json(400, "Only .ply splat files can be imported.")
        name = (query.get("name") or Path(filename).stem).strip()[:80] or "Imported scan"
        up = query.get("up") if query.get("up") in scene.UP_CHOICES else "-y"
        sid = scene.new_scene_id(name)
        d = config.SCENES_DIR / sid
        d.mkdir(parents=True)
        try:
            self.read_body_to(d / "splat_import.ply")
            meta = {"name": name, "kind": "import", "up": up, "created": time.time(),
                    "source": filename}
            config.write_json(d / "meta.json", meta)
            scene.publish_import(d, meta, d / "splat_import.ply")
            config.write_json(d / "status.json", {"state": "done"})
        except Exception as exc:
            shutil.rmtree(d, ignore_errors=True)
            return self.send_error_json(400, "Could not read that file: %s" % exc)
        return self.send_json(scene_summary(d))


def lan_address() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def serve(port: int = 8765, lan: bool = False, open_browser: bool = True):
    global JOBS
    config.SCENES_DIR.mkdir(parents=True, exist_ok=True)
    JOBS = Jobs()
    try:
        scene.create_demo(config.SCENES_DIR)
    except Exception as exc:
        print("(could not create the example room: %s)" % exc)

    host = "0.0.0.0" if lan else "127.0.0.1"
    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("Could not find a free port near %d." % port)
    httpd.daemon_threads = True

    url = "http://localhost:%d/" % port
    print("\n  Roomwalk is running.\n\n  Open this in your browser:  %s" % url)
    if lan:
        ip = lan_address()
        if ip:
            print("  On a phone or tablet on the same Wi-Fi:  http://%s:%d/" % (ip, port))
    print("\n  Keep this window open while you use the app. Close it to stop.\n")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for sid in list(JOBS.running):
            JOBS.cancel(sid)

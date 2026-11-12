"""Stage 3: train the 3D scan (Gaussian splatting) with Brush.

Brush (https://github.com/ArthurBrussee/brush, Apache-2.0) is a free trainer
that runs on ordinary graphics chips - Intel, AMD, NVIDIA and Apple - which is
why it is used here instead of the usual NVIDIA-only tools. It is a single
program; this module downloads it on first use and drives it.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import config

BRUSH_VERSION = "v0.3.0"  # the version this app's commands were checked against
BRUSH_REPO = "ArthurBrussee/brush"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _asset_name() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "brush-app-x86_64-pc-windows-msvc.zip"
    if sys.platform == "darwin":
        if machine not in ("arm64", "aarch64"):
            raise RuntimeError(
                "Brush has no ready-made download for Intel Macs. Build it from "
                "source (see its GitHub page) and put the program in the tools/brush folder."
            )
        return "brush-app-aarch64-apple-darwin.tar.xz"
    return "brush-app-x86_64-unknown-linux-gnu.tar.xz"


def _is_brush(path: Path) -> bool:
    name = path.name.lower()
    stem = name[:-4] if name.endswith(".exe") else name
    if sys.platform == "win32" and not name.endswith(".exe"):
        return False
    return path.is_file() and stem in ("brush_app", "brush", "brush-app")


def find_brush() -> Path | None:
    """Look for the Brush program: env var, the tools folder, then PATH."""
    env = os.environ.get("ROOMWALK_BRUSH")
    if env and Path(env).is_file():
        return Path(env)
    folder = config.TOOLS_DIR / "brush"
    if folder.is_dir():
        found = sorted(p for p in folder.rglob("*") if _is_brush(p))
        if found:
            return found[0]
    for name in ("brush_app", "brush"):
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    return None


def _download(url: str, dest: Path, report) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "roomwalk"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            block = resp.read(1 << 18)
            if not block:
                break
            out.write(block)
            got += len(block)
            if total:
                report(got / total, "Downloading the 3D engine (%d of %d MB)"
                       % (got >> 20, total >> 20))


def install_brush(report) -> Path:
    """Download Brush into tools/brush. Tries the pinned release first, then
    whatever asset the GitHub release list offers for this computer."""
    asset = _asset_name()
    folder = config.TOOLS_DIR / "brush"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / asset
    urls = ["https://github.com/%s/releases/download/%s/%s" % (BRUSH_REPO, BRUSH_VERSION, asset)]
    errors = []
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/%s/releases/tags/%s" % (BRUSH_REPO, BRUSH_VERSION),
            headers={"User-Agent": "roomwalk", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            triple = asset.split("brush-app-")[1].split(".")[0]
            for item in json.load(resp).get("assets", []):
                if triple in item["name"] and not item["name"].endswith((".sha256", ".json")):
                    if item["browser_download_url"] not in urls:
                        urls.append(item["browser_download_url"])
    except Exception as exc:  # no network, rate limit ... the pinned URL may still work
        errors.append("release list: %s" % exc)
    urls.append("https://github.com/%s/releases/latest/download/%s" % (BRUSH_REPO, asset))

    for url in urls:
        try:
            archive = folder / url.rsplit("/", 1)[1]
            _download(url, archive, report)
            if archive.name.endswith(".zip"):
                with zipfile.ZipFile(archive) as z:
                    z.extractall(folder)
            else:
                with tarfile.open(archive) as t:
                    try:
                        t.extractall(folder, filter="data")
                    except TypeError:  # Python < 3.12
                        t.extractall(folder)
            archive.unlink()
            exe = find_brush()
            if exe:
                if sys.platform != "win32":
                    exe.chmod(0o755)
                return exe
            errors.append("%s: archive had no brush program inside" % url)
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
    raise RuntimeError(
        "Could not download the 3D engine (Brush) automatically.\n"
        "Download it by hand from https://github.com/%s/releases (the file named %s), "
        "unzip it, and put its contents in this folder:\n  %s\nThen press Retry.\n\nDetails:\n%s"
        % (BRUSH_REPO, asset, folder, "\n".join(errors))
    )


def _supported_flags(exe: Path) -> set[str]:
    """Flags this Brush build understands, read from its --help text, so a
    newer or older Brush still gets only options it knows."""
    try:
        out = subprocess.run([str(exe), "--help"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60,
                             creationflags=NO_WINDOW)
        return set(re.findall(r"(--[a-z][a-z0-9-]+)", out.stdout + out.stderr))
    except Exception:
        return set()


def train(exe: Path, dataset_dir: Path, export_dir: Path, preset: dict, report,
          on_export, log_path: Path) -> Path:
    """Run Brush on a COLMAP dataset folder. Calls on_export(path, step) each
    time a snapshot of the scan is written; it may move the file and return
    the new path. Returns the path of the last snapshot."""
    shutil.rmtree(export_dir, ignore_errors=True)
    export_dir.mkdir(parents=True)
    steps = int(preset["steps"])
    export_every = max(500, steps // 12)
    wanted = [
        ("--total-steps", steps),
        ("--max-splats", preset["max_splats"]),
        ("--max-resolution", preset["train_size"]),
        ("--sh-degree", preset["sh_degree"]),
        ("--export-every", export_every),
        ("--export-path", str(export_dir)),
        ("--export-name", "export_{iter}.ply"),
    ]
    known = _supported_flags(exe)
    cmd = [str(exe), str(dataset_dir)]
    for flag, value in wanted:
        if not known or flag in known:
            cmd += [flag, str(value)]
    # (no --with-viewer: Brush runs without a window when given a folder)

    # Only Brush's own progress lines; its other "info" output is enormous.
    env = dict(os.environ, RUST_LOG="warn,brush_cli=info,brush_app=info", RUST_BACKTRACE="1")
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    log.write("\n$ %s\n" % " ".join(cmd))
    log.flush()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env, cwd=str(export_dir),
                            creationflags=NO_WINDOW)
    state = {"step": 0, "splats": 0, "tail": []}

    def pump():
        for line in proc.stdout:
            line = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line).rstrip()[:600]
            if not line:
                continue
            log.write(line + "\n")
            log.flush()
            state["tail"] = (state["tail"] + [line])[-25:]
            m = re.search(r"Refine iter (\d+), (\d+) splats", line)
            if m:
                state["step"] = max(state["step"], int(m.group(1)))
                state["splats"] = int(m.group(2))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    started = time.time()
    sizes, published = {}, set()
    final = None

    def sweep(require_stable: bool):
        nonlocal final
        for f in sorted(export_dir.glob("export_*.ply")):
            m = re.search(r"export_(\d+)\.ply$", f.name)
            if not m or f.name in published:
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            # a snapshot counts once its size has stopped changing between looks
            if size > 0 and (not require_stable or sizes.get(f.name) == size):
                published.add(f.name)
                state["step"] = max(state["step"], int(m.group(1)))
                final = on_export(f, int(m.group(1))) or f
            sizes[f.name] = size

    while True:
        code = proc.poll()
        sweep(require_stable=code is None)
        step = state["step"]
        elapsed = time.time() - started
        if step > 0:
            eta = "about %s left" % _nice_time(elapsed / step * (steps - step))
        else:
            eta = "warming up the graphics chip"
        detail = "%d of %d steps" % (step, steps)
        if state["splats"]:
            detail += ", %s splats" % format(state["splats"], ",")
        report(min(0.99, step / steps), "Building the 3D scan: %s (%s)" % (detail, eta))
        if code is not None:
            break
        time.sleep(2.0)

    reader.join(timeout=5)
    log.close()
    if proc.returncode != 0 or final is None:
        tail = "\n".join(state["tail"][-12:])
        hint = ""
        low = tail.lower()
        if "memory" in low or "allocation" in low or "device lost" in low or "oom" in low:
            hint = ("\nThe graphics chip ran out of memory. Try the Quick quality, "
                    "and close other programs (especially browser tabs and games).")
        elif "adapter" in low or "vulkan" in low or "backend" in low:
            hint = ("\nThe 3D engine could not use this computer's graphics chip. Update the "
                    "graphics driver (for Intel graphics: Intel's Driver & Support Assistant), "
                    "restart, and press Retry.")
        raise RuntimeError("The 3D engine stopped before finishing (exit code %s).%s\n\nLast lines:\n%s"
                           % (proc.returncode, hint, tail or "(no output)"))
    return final


def _nice_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "%d seconds" % seconds
    if seconds < 5400:
        return "%d minutes" % round(seconds / 60)
    return "%.1f hours" % (seconds / 3600)

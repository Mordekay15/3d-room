"""Stage 1: pull the sharpest still frames out of the video."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def ffmpeg_exe() -> str:
    """ffmpeg bundled with the imageio-ffmpeg package (no separate install)."""
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(video: Path) -> dict:
    """Read duration, size, frame rate and HDR-ness by parsing ffmpeg's banner."""
    out = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    ).stderr
    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 30.0, "hdr": False, "has_video": False}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in out.splitlines():
        if "Video:" not in line:
            continue
        info["has_video"] = True
        m = re.search(r"[ ,](\d{2,5})x(\d{2,5})[ ,\[]", line)
        if m:
            info["width"], info["height"] = int(m.group(1)), int(m.group(2))
        m = re.search(r"(\d+(?:\.\d+)?)\s*fps", line)
        if m:
            info["fps"] = float(m.group(1))
        info["hdr"] = bool(re.search(r"arib-std-b67|smpte2084", line))
        break
    # Phone videos shot upright are stored sideways plus a rotation note.
    if re.search(r"rotation of -?(90|270)", out) or re.search(r"rotate\s*:\s*(90|270)", out):
        info["width"], info["height"] = info["height"], info["width"]
    return info


def _sharpness(path: Path) -> float:
    """Variance of a Laplacian filter: high for crisp frames, low for motion blur."""
    with Image.open(path) as im:
        im.draft("L", (im.width // 2, im.height // 2))  # fast, reduced-size JPEG decode
        g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def extract_frames(video: Path, out_dir: Path, target: int, long_side: int, report) -> dict:
    """Write about `target` sharp frames to out_dir as frame_00001.jpg, ...

    Three times as many candidates are decoded, then the sharpest one in each
    time slot is kept, so blurry moments are skipped without leaving gaps.
    """
    info = probe(video)
    if not info["has_video"] or info["duration"] <= 0:
        raise RuntimeError("This file does not look like a video. Try an .mp4 or .mov file.")
    if info["duration"] < 4:
        raise RuntimeError(
            "The video is only %.0f seconds long. Film at least 30 seconds, "
            "walking slowly around the whole room." % info["duration"]
        )

    cand_dir = out_dir.parent / "candidates"
    shutil.rmtree(cand_dir, ignore_errors=True)
    shutil.rmtree(out_dir, ignore_errors=True)
    cand_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    rate = min(info["fps"], 3.0 * target / info["duration"])
    expected = max(1, int(info["duration"] * rate))
    scale = (
        "scale='if(gt(iw,ih),min(%d,iw),-2)':'if(gt(iw,ih),-2,min(%d,ih))'" % (long_side, long_side)
    )
    tonemap = (
        "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
        "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,"
    )
    attempts = [tonemap, ""] if info["hdr"] else [""]
    last_err = ""
    for prefix in attempts:
        for f in cand_dir.glob("*.jpg"):
            f.unlink()
        cmd = [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            "-an", "-vf", "%sfps=%.5f,%s" % (prefix, rate, scale),
            "-q:v", "2", str(cand_dir / "c_%05d.jpg"),
        ]
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW)
        while proc.poll() is None:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            done = sum(1 for _ in cand_dir.glob("*.jpg"))
            report(0.7 * min(1.0, done / expected), "Reading the video (%d frames so far)" % done)
        last_err = (proc.stderr.read() or "").strip()
        if proc.returncode == 0 and any(cand_dir.glob("*.jpg")):
            break
    candidates = sorted(cand_dir.glob("*.jpg"))
    if not candidates:
        raise RuntimeError("Could not read frames from the video. ffmpeg said: %s" % last_err[-400:])

    scores = []
    for i, path in enumerate(candidates):
        scores.append(_sharpness(path))
        if i % 10 == 0:
            report(0.7 + 0.25 * i / len(candidates), "Checking which frames are sharp")
    scores = np.array(scores)

    slots = np.array_split(np.arange(len(candidates)), min(target, len(candidates)))
    chosen = [int(slot[np.argmax(scores[slot])]) for slot in slots if len(slot)]
    for n, idx in enumerate(chosen, start=1):
        shutil.move(str(candidates[idx]), str(out_dir / ("frame_%05d.jpg" % n)))
    shutil.rmtree(cand_dir, ignore_errors=True)

    with Image.open(out_dir / "frame_00001.jpg") as im:
        info["frame_width"], info["frame_height"] = im.size
    info["frames"] = len(chosen)
    info["blurry_share"] = float((scores[chosen] < 0.35 * np.median(scores[chosen])).mean())
    report(1.0, "Kept %d sharp frames" % len(chosen))
    return info


def make_thumbnail(images_dir: Path, dest: Path, width: int = 640) -> None:
    frames = sorted(images_dir.glob("*.jpg"))
    if not frames:
        return
    with Image.open(frames[len(frames) // 3]) as im:
        im.thumbnail((width, width))
        im.convert("RGB").save(dest, quality=82)

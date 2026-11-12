"""Paths, quality presets and small shared helpers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SCENES_DIR = Path(os.environ.get("ROOMWALK_SCENES", ROOT / "scenes"))
TOOLS_DIR = Path(os.environ.get("ROOMWALK_TOOLS", ROOT / "tools"))

# Height of the phone above the floor while filming, in metres. The scan has no
# real-world units on its own, so this is what sets its size.
DEFAULT_CAMERA_HEIGHT = 1.4

# Quality presets. They are sized for laptops with built-in graphics
# (Intel Iris Xe and similar): the limits that matter there are graphics
# memory (number of splats, picture size) and patience (training steps).
PRESETS = {
    "quick": {
        "label": "Quick",
        "blurb": "A first look. Soft detail, but the fastest way to check a video works.",
        "frames": 110,
        "frame_size": 1280,
        "train_size": 960,
        "steps": 5000,
        "max_splats": 300_000,
        "sh_degree": 2,
    },
    "standard": {
        "label": "Standard",
        "blurb": "Good detail for one room. The sensible default.",
        "frames": 170,
        "frame_size": 1600,
        "train_size": 1280,
        "steps": 12000,
        "max_splats": 700_000,
        "sh_degree": 2,
    },
    "detailed": {
        "label": "Detailed",
        "blurb": "Sharpest result. Much slower; best left running overnight on a laptop.",
        "frames": 240,
        "frame_size": 1600,
        "train_size": 1600,
        "steps": 30000,
        "max_splats": 1_500_000,
        "sh_degree": 3,
    },
}
DEFAULT_PRESET = "standard"

STAGES = [
    ("frames", "Pick sharp frames"),
    ("poses", "Work out where the camera was"),
    ("train", "Build the 3D scan"),
    ("finish", "Make it walkable"),
]


def write_json(path: Path, data) -> None:
    """Write JSON atomically (write to a temp file, then swap it in)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: someone is reading the file right now
            time.sleep(0.05)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

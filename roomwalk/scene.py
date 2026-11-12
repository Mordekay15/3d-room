"""Writing a finished (or in-progress) scan in the form the viewer reads:
scene.json + the splat file + map images, all inside the scan's folder."""

from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from . import align, config, ply

UP_CHOICES = {
    "-y": [0.0, -1.0, 0.0],  # how most splat files are stored
    "+y": [0.0, 1.0, 0.0],
    "+z": [0.0, 0.0, 1.0],
    "-z": [0.0, 0.0, -1.0],
}


def new_scene_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "room"
    return "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), slug)


def _write_scene(scene_dir: Path, meta: dict, splat_file: str, version: int, count: int,
                 matrix: np.ndarray, scale: float, map_info: dict, path: list | None,
                 quality: str, extra: dict | None = None) -> dict:
    start = align.pick_start(map_info, scene_dir, path)
    if path and len(path) > 600:
        path = path[:: int(np.ceil(len(path) / 600))]
    old = config.read_json(scene_dir / "scene.json", {}) or {}
    data = {
        "id": scene_dir.name,
        "name": meta.get("name", scene_dir.name),
        "kind": meta.get("kind", "scan"),
        "quality": quality,  # preview | training | final
        "splat": {"file": splat_file, "version": int(version), "count": int(count)},
        "matrix": [round(float(v), 7) for v in np.asarray(matrix).T.flatten()],  # column-major
        "scale": scale,
        "map": map_info,
        "start": start,
        "path": path or [],
        "eye_height": old.get("eye_height", 1.6),
        "updated": time.time(),
    }
    data.update(extra or {})
    config.write_json(scene_dir / "scene.json", data)
    # tidy away superseded snapshots (best effort: a browser may still be reading one)
    for f in scene_dir.glob("splat_*.ply"):
        if f.name != splat_file:
            try:
                f.unlink()
            except OSError:
                pass
    return data


def publish_scan(scene_dir: Path, meta: dict, splat_path: Path | None, version: int,
                 quality: str) -> dict:
    """Align a scan made from a video and write everything the viewer needs.

    splat_path None -> only the sparse points from camera tracking exist yet;
    a rough dotted preview is written so the room can already be walked.
    """
    from . import poses

    model = poses.load_model(scene_dir / "dataset" / "sparse" / "0")
    cam_height = float(meta.get("camera_height", config.DEFAULT_CAMERA_HEIGHT))

    if splat_path is None:
        pts, weights, rgb = model["xyz"], np.ones(len(model["xyz"])), model["rgb"]
        dense = False
    else:
        splats = ply.read_splats(splat_path, max_points=3_000_000)
        solid = splats["opacity"] > 0.25
        pts, weights, rgb = splats["xyz"][solid], splats["opacity"][solid], splats["rgb"][solid]
        count = splats["count"]
        dense = True
        if len(pts) < 2000:  # training barely started: fall back to tracking points
            pts, weights, rgb = model["xyz"], np.ones(len(model["xyz"])), model["rgb"]
            dense = False

    solve_pts = pts if len(pts) <= 400_000 else pts[:: len(pts) // 400_000]
    fit = align.solve_alignment(np.asarray(solve_pts, np.float64), model["centers"],
                                model["down"], cam_height)
    M = fit["matrix"]
    path = align.camera_path(M, model["centers"], model["forward"])
    path_xz = np.array([[p[0], p[1]] for p in path])

    if splat_path is None:
        size = 0.014 / fit["scale"]  # about 1.4 cm dots, in the scan's own units
        splat_path = scene_dir / "splat_preview.ply"
        ply.write_splats(splat_path, model["xyz"], model["rgb"], np.full((1, 3), size), opacity=0.9)
        count = len(model["xyz"])

    map_info = align.build_maps(align.apply(M, np.asarray(pts, np.float64)), weights, rgb,
                                path_xz, scene_dir, dense=dense)
    return _write_scene(scene_dir, meta, splat_path.name, version, count, M, fit["scale"],
                        map_info, path, quality,
                        {"frames": len(model["names"])})


def publish_import(scene_dir: Path, meta: dict, splat_path: Path) -> dict:
    """Prepare a splat file that was made elsewhere (no camera path known)."""
    splats = ply.read_splats(splat_path, max_points=3_000_000)
    solid = splats["opacity"] > 0.25
    pts = np.asarray(splats["xyz"][solid], np.float64)
    if len(pts) < 100:
        raise RuntimeError("This file has too few solid points to be a room scan.")
    up = UP_CHOICES.get(meta.get("up", "-y"), UP_CHOICES["-y"])
    solve_pts = pts if len(pts) <= 400_000 else pts[:: len(pts) // 400_000]
    fit = align.solve_alignment(solve_pts, None, None, 0.0, up_hint=np.array(up))
    M = fit["matrix"]
    map_info = align.build_maps(align.apply(M, pts), splats["opacity"][solid], splats["rgb"][solid],
                                None, scene_dir, dense=True)
    return _write_scene(scene_dir, meta, splat_path.name, int(time.time()), splats["count"], M,
                        fit["scale"], map_info, None, "final")


def create_demo(scenes_dir: Path) -> Path:
    """Generate the built-in example room (once)."""
    from . import demo

    scene_dir = scenes_dir / "demo-room"
    if (scene_dir / "scene.json").exists():
        return scene_dir
    scene_dir.mkdir(parents=True, exist_ok=True)
    xyz, rgb, scales = demo.build_demo_room()
    ply.write_splats(scene_dir / "splat_demo.ply", xyz, rgb, scales, opacity=0.97)
    meta = {"name": "Example room (drawn by code, not a scan)", "kind": "demo",
            "created": time.time()}
    config.write_json(scene_dir / "meta.json", meta)
    map_info = align.build_maps(xyz, np.ones(len(xyz)), rgb, None, scene_dir, dense=True)
    data = _write_scene(scene_dir, meta, "splat_demo.ply", 1, len(xyz), np.eye(4), 1.0,
                        map_info, None, "final")
    data["start"] = {"x": 1.6, "z": 0.2, "yaw": 1.1}
    config.write_json(scene_dir / "scene.json", data)
    config.write_json(scene_dir / "status.json", {"state": "done"})
    return scene_dir

"""Stage 2: work out where the camera was for every frame (structure from motion).

Uses COLMAP through the `pycolmap` package, on the processor only, so it needs
no particular graphics card.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np


def _pair_list(names: list[str], overlap: int, loop_step: int) -> list[tuple[str, str]]:
    """Which frames to compare with which.

    - every frame with its next `overlap` neighbours (the camera moved a little)
    - every frame with frames 16, 32, 64 ... later (cheap long-range links)
    - every `loop_step`-th frame with every other such frame, so that coming
      back to a spot seen earlier (walking a loop of the room) is recognised
      and the walls line up instead of doubling.
    """
    n = len(names)
    pairs = set()
    for i in range(n):
        for d in range(1, overlap + 1):
            if i + d < n:
                pairs.add((i, i + d))
        d = 16
        while i + d < n:
            pairs.add((i, i + d))
            d *= 2
    keys = list(range(0, n, loop_step))
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            pairs.add((keys[a], keys[b]))
    return [(names[i], names[j]) for i, j in sorted(pairs)]


def solve_poses(images_dir: Path, work_dir: Path, dataset_dir: Path, report,
                max_features: int = 4096) -> dict:
    """Run feature extraction, matching and mapping; write an undistorted
    dataset (images + sparse/0) to dataset_dir. Returns a small summary."""
    import pycolmap

    threads = max(1, (os.cpu_count() or 2))
    names = sorted(p.name for p in images_dir.glob("*.jpg"))
    if len(names) < 12:
        raise RuntimeError("Only %d usable frames - the video is too short." % len(names))

    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True)
    db = work_dir / "database.db"

    # --- 1. find distinctive points in every frame ---------------------------
    ext = pycolmap.FeatureExtractionOptions()
    ext.use_gpu = False
    ext.num_threads = threads
    ext.sift.max_num_features = max_features
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"  # one lens, mild distortion: right for a phone video
    batch = 8
    for start in range(0, len(names), batch):
        if start > 0:
            reader.existing_camera_id = 1  # all frames come from the same camera
        pycolmap.extract_features(
            db, images_dir, image_names=names[start:start + batch],
            camera_mode=pycolmap.CameraMode.SINGLE, reader_options=reader,
            extraction_options=ext, device=pycolmap.Device.cpu,
        )
        done = min(len(names), start + batch)
        report(0.25 * done / len(names), "Finding landmarks in frame %d of %d" % (done, len(names)))

    # --- 2. match those points between frames --------------------------------
    pairs = _pair_list(names, overlap=10, loop_step=max(4, len(names) // 28))
    match = pycolmap.FeatureMatchingOptions()
    match.use_gpu = False
    match.num_threads = threads
    chunk = 120
    for start in range(0, len(pairs), chunk):
        pair_file = work_dir / "pairs.txt"
        pair_file.write_text(
            "".join("%s %s\n" % p for p in pairs[start:start + chunk]), encoding="ascii"
        )
        pairing = pycolmap.ImportedPairingOptions()
        pairing.match_list_path = str(pair_file)
        pycolmap.match_image_pairs(
            db, matching_options=match, pairing_options=pairing, device=pycolmap.Device.cpu
        )
        done = min(len(pairs), start + chunk)
        report(0.25 + 0.3 * done / len(pairs),
               "Matching landmarks between frames (%d%%)" % (100 * done // len(pairs)))

    # --- 3. solve for camera positions and 3D points -------------------------
    sparse_root = work_dir / "sparse"
    sparse_root.mkdir()
    opts = pycolmap.IncrementalPipelineOptions()
    opts.num_threads = threads
    opts.multiple_models = True
    opts.min_model_size = 8
    counter = {"n": 2}

    def on_image():
        counter["n"] += 1
        report(0.55 + 0.37 * min(1.0, counter["n"] / len(names)),
               "Placing the camera: %d of %d frames" % (min(counter["n"], len(names)), len(names)))

    report(0.55, "Placing the camera: starting")
    models = pycolmap.incremental_mapping(db, images_dir, sparse_root, options=opts,
                                          next_image_callback=on_image)
    if not models:
        raise RuntimeError(
            "Could not work out the camera path from this video. This usually means "
            "the camera moved too fast, the room is too dark, or the walls are plain "
            "with nothing for the app to track. Film slower, with lights on, and keep "
            "furniture and wall details in view."
        )
    best_id = max(models, key=lambda k: models[k].num_reg_images())
    best = models[best_id]
    registered = best.num_reg_images()
    if registered < max(10, 0.25 * len(names)):
        raise RuntimeError(
            "Only %d of %d frames could be placed, which is not enough for a scan. "
            "Film one continuous, slow walk around the room, and avoid pointing the "
            "camera at blank walls or out of windows for long." % (registered, len(names))
        )

    # --- 4. remove lens distortion so the trainer gets ideal pinhole pictures -
    report(0.93, "Straightening the frames")
    shutil.rmtree(dataset_dir, ignore_errors=True)
    pycolmap.undistort_images(dataset_dir, sparse_root / str(best_id), images_dir,
                              output_type="COLMAP")
    und_sparse = dataset_dir / "sparse"
    final_sparse = und_sparse / "0"
    final_sparse.mkdir(exist_ok=True)
    for f in list(und_sparse.iterdir()):
        if f.is_file():
            shutil.move(str(f), str(final_sparse / f.name))
    shutil.rmtree(dataset_dir / "stereo", ignore_errors=True)
    for f in dataset_dir.glob("run-colmap-*"):
        f.unlink()
    # The trainer looks for "cameras.bin" anywhere below the folder it is given,
    # so the working copy (with lens distortion) must not sit next to it.
    shutil.rmtree(work_dir, ignore_errors=True)

    report(1.0, "Camera placed in %d of %d frames" % (registered, len(names)))
    return {
        "frames": len(names),
        "registered": int(registered),
        "points": int(best.num_points3D()),
        "partial": bool(registered < 0.7 * len(names)),
    }


def load_model(sparse_dir: Path) -> dict:
    """Read camera poses and 3D points from a COLMAP model folder.

    Returns dict with, per frame (sorted by file name): centers (N,3),
    down (N,3) and forward (N,3) camera axes in world space; plus the sparse
    points xyz (M,3) and rgb (M,3) 0..1.
    """
    import pycolmap

    rec = pycolmap.Reconstruction(str(sparse_dir))
    images = sorted((im for im in rec.images.values() if im.has_pose), key=lambda im: im.name)
    centers, down, forward, names = [], [], [], []
    for im in images:
        cfw = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
        R = np.asarray(cfw.rotation.matrix())  # camera-from-world
        centers.append(np.asarray(im.projection_center()))
        down.append(R[1])      # the image's "down" direction, in world space
        forward.append(R[2])   # where the lens points, in world space
        names.append(im.name)
    xyz, rgb = [], []
    for p in rec.points3D.values():
        if p.track.length() >= 3 and p.error < 2.5:
            xyz.append(p.xyz)
            rgb.append(p.color)
    if len(xyz) < 50:  # tiny model: take everything
        xyz = [p.xyz for p in rec.points3D.values()]
        rgb = [p.color for p in rec.points3D.values()]
    return {
        "names": names,
        "centers": np.array(centers, np.float64).reshape(-1, 3),
        "down": np.array(down, np.float64).reshape(-1, 3),
        "forward": np.array(forward, np.float64).reshape(-1, 3),
        "xyz": np.array(xyz, np.float64).reshape(-1, 3),
        "rgb": np.array(rgb, np.float64).reshape(-1, 3) / 255.0,
    }

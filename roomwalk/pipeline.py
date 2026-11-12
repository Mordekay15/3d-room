"""The video -> walkable scan pipeline. Runs as its own process, one per scan:

    python -m roomwalk.pipeline <scan folder>

Progress goes to status.json in the scan folder, which the app's web page
polls. Every stage skips itself if its result is already on disk, so a failed
or interrupted scan picks up where it stopped when retried.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from pathlib import Path

from . import config


class Status:
    """Progress record shared with the web page (status.json)."""

    def __init__(self, scene_dir: Path):
        self.path = scene_dir / "status.json"
        self.data = {
            "state": "running", "stage": None, "message": "Starting", "error": None,
            "pid": os.getpid(), "started": time.time(), "updated": time.time(),
            "stages": {key: {"state": "pending", "progress": 0.0, "seconds": 0.0}
                       for key, _ in config.STAGES},
            "notes": [],
        }
        self._last_write = 0.0
        self._stage_started = 0.0
        self.save(force=True)

    def save(self, force=False):
        now = time.time()
        if not force and now - self._last_write < 0.4:
            return
        self._last_write = now
        self.data["updated"] = now
        config.write_json(self.path, self.data)

    def begin(self, stage: str, message: str):
        self._stage_started = time.time()
        self.data["stage"] = stage
        self.data["message"] = message
        self.data["stages"][stage].update(state="running", progress=0.0)
        self.save(force=True)

    def report(self, progress: float, message: str):
        stage = self.data["stage"]
        self.data["message"] = message
        self.data["stages"][stage]["progress"] = round(float(progress), 4)
        self.data["stages"][stage]["seconds"] = round(time.time() - self._stage_started, 1)
        self.save()

    def end(self, stage: str, skipped=False):
        entry = self.data["stages"][stage]
        entry.update(state="done", progress=1.0)
        if not skipped:
            entry["seconds"] = round(time.time() - self._stage_started, 1)
        self.save(force=True)

    def note(self, text: str):
        if text not in self.data["notes"]:
            self.data["notes"].append(text)
        self.save(force=True)

    def finish(self, state: str, message: str, error: str | None = None):
        if error and self.data["stage"]:
            self.data["stages"][self.data["stage"]]["state"] = "failed"
        self.data.update(state=state, message=message, error=error)
        self.save(force=True)


def run(scene_dir: Path) -> int:
    scene_dir = Path(scene_dir).resolve()
    meta = config.read_json(scene_dir / "meta.json", {}) or {}
    preset = dict(config.PRESETS.get(meta.get("preset"), config.PRESETS[config.DEFAULT_PRESET]))
    # Advanced: "overrides" in meta.json can change any preset number for one scan,
    # e.g. {"overrides": {"steps": 20000, "max_splats": 1000000}}
    preset.update({k: v for k, v in (meta.get("overrides") or {}).items() if k in preset})
    status = Status(scene_dir)
    log_path = scene_dir / "log.txt"

    def log(text: str):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), text))

    log("Pipeline started (preset: %s)" % meta.get("preset"))
    try:
        from . import frames, poses, scene, trainer

        images_dir = scene_dir / "images"
        dataset_dir = scene_dir / "dataset"
        cameras_file = dataset_dir / "sparse" / "0" / "cameras.bin"
        final_marker = scene_dir / "trained.json"

        # ---- 1. frames -------------------------------------------------------
        frames_info = config.read_json(scene_dir / "frames.json")
        if frames_info and (cameras_file.exists() or any(images_dir.glob("*.jpg"))):
            status.end("frames", skipped=True)
        else:
            status.begin("frames", "Reading the video")
            videos = [p for p in scene_dir.glob("video.*")]
            if not videos:
                raise RuntimeError("The uploaded video is missing from the scan folder.")
            frames_info = frames.extract_frames(videos[0], images_dir, preset["frames"],
                                                preset["frame_size"], status.report)
            config.write_json(scene_dir / "frames.json", frames_info)
            frames.make_thumbnail(images_dir, scene_dir / "thumb.jpg")
            log("Frames: %s" % frames_info)
            if frames_info["duration"] < 25:
                status.note("The video is short (%d s). 45-90 seconds of slow walking gives a "
                            "much more complete room." % frames_info["duration"])
            if frames_info.get("blurry_share", 0) > 0.25:
                status.note("Many frames are blurry. Move the phone more slowly and use more light.")
            status.end("frames")

        # ---- 2. camera poses -------------------------------------------------
        if cameras_file.exists():
            status.end("poses", skipped=True)
        else:
            status.begin("poses", "Finding landmarks in the frames")
            summary = poses.solve_poses(images_dir, scene_dir / "colmap", dataset_dir, status.report)
            log("Poses: %s" % summary)
            if summary["partial"]:
                status.note("Only %d of %d frames could be placed, so parts of the room may be "
                            "missing or fuzzy." % (summary["registered"], summary["frames"]))
            status.end("poses")
            shutil.rmtree(images_dir, ignore_errors=True)  # straightened copies live in dataset/
        if not (scene_dir / "scene.json").exists():
            scene.publish_scan(scene_dir, meta, None, 0, "preview")
            log("Dotted preview published")

        # ---- 3. training -----------------------------------------------------
        trained = config.read_json(final_marker)
        if trained and (scene_dir / trained["file"]).exists():
            status.end("train", skipped=True)
            final_splat = scene_dir / trained["file"]
        else:
            status.begin("train", "Getting the 3D engine ready")
            exe = trainer.find_brush()
            if exe is None:
                exe = trainer.install_brush(lambda p, m: status.report(0.0, m))
                log("Installed Brush at %s" % exe)

            def on_export(path: Path, step: int):
                """Move each snapshot next to scene.json and show it in the viewer."""
                dest = scene_dir / ("splat_%06d.ply" % step)
                try:
                    shutil.move(str(path), str(dest))
                except OSError:
                    return None  # still being written; picked up on the next look
                try:
                    quality = "final" if step >= preset["steps"] else "training"
                    scene.publish_scan(scene_dir, meta, dest, step, quality)
                    log("Snapshot at step %d published" % step)
                except Exception as exc:  # a bad snapshot must not stop training
                    log("Snapshot %d could not be published: %s" % (step, exc))
                return dest

            final_splat = trainer.train(exe, dataset_dir, scene_dir / "export", preset,
                                        status.report, on_export, log_path)
            config.write_json(final_marker, {"file": final_splat.name, "steps": preset["steps"]})
            shutil.rmtree(scene_dir / "export", ignore_errors=True)
            status.end("train")

        # ---- 4. make it walkable --------------------------------------------
        status.begin("finish", "Finding the floor and drawing the map")
        data = scene.publish_scan(scene_dir, meta, final_splat, preset["steps"], "final")
        log("Final scene: %d splats, map %dx%d" % (
            data["splat"]["count"], data["map"]["width"], data["map"]["height"]))
        status.end("finish")
        status.finish("done", "Your room is ready")
        return 0
    except Exception as exc:
        log("FAILED: %s\n%s" % (exc, traceback.format_exc()))
        status.finish("failed", "Stopped", error=str(exc))
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m roomwalk.pipeline <scan folder>")
        sys.exit(2)
    sys.exit(run(Path(sys.argv[1])))

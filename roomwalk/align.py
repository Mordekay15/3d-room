"""Turn a raw scan into a room you can stand in.

A scan comes out in arbitrary units, tilted any which way. This module finds
the floor, sets the real-world size, squares the walls to the map, and draws
the top-down floor plan and the "you can't walk here" grid.

Result space (what the viewer uses): metres, Y up, floor at y = 0.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

CELL = 0.04  # metres per map/collision cell


def _unit(v):
    return v / (np.linalg.norm(v) + 1e-12)


def find_floor(points: np.ndarray, up: np.ndarray, cam_height_level: float | None):
    """Find the floor plane. Returns (refined up vector, floor height along it).

    The floor is the lowest well-populated horizontal layer below the camera.
    """
    up = _unit(up)
    h = points @ up
    below = h if cam_height_level is None else h[h < cam_height_level]
    if len(below) < 40:
        return up, float(np.percentile(h, 2.0))
    lo = float(np.percentile(below, 0.5))
    hi = float(cam_height_level) if cam_height_level is not None else float(np.percentile(below, 60))
    if hi - lo < 1e-9:
        return up, lo
    hist, edges = np.histogram(below, bins=120, range=(lo, hi))
    smooth = np.convolve(hist.astype(np.float64), [1, 2, 3, 2, 1], mode="same")
    strong = np.nonzero(smooth >= 0.12 * smooth.max())[0]
    if len(strong) == 0:
        return up, float(np.percentile(below, 1.5))
    i = int(strong[0])
    while i + 1 < len(smooth) and smooth[i + 1] >= smooth[i]:
        i += 1  # climb to the top of this first peak
    floor = 0.5 * (edges[i] + edges[i + 1])

    # Refine: fit a plane through the points in that layer.
    band = 2.5 * (hi - lo) / 120
    sel = points[np.abs(h - floor) < band]
    if len(sel) >= 40:
        if len(sel) > 20000:
            sel = sel[:: len(sel) // 20000]
        centred = sel - sel.mean(axis=0)
        normal = np.linalg.svd(centred, full_matrices=False)[2][2]
        if normal @ up < 0:
            normal = -normal
        if normal @ up > np.cos(np.radians(14)):
            up = _unit(normal)
            floor = float(np.median(sel @ up))
    return up, float(floor)


def _square_up(xz: np.ndarray) -> float:
    """Angle (radians) that best lines the walls up with the map's axes."""
    if len(xz) < 200:
        return 0.0
    if len(xz) > 60000:
        xz = xz[:: len(xz) // 60000]
    best, best_score = 0.0, -1.0
    for deg in np.arange(0.0, 90.0, 1.0):
        a = np.radians(deg)
        c, s = np.cos(a), np.sin(a)
        x = xz[:, 0] * c - xz[:, 1] * s
        z = xz[:, 0] * s + xz[:, 1] * c
        score = 0.0
        for v in (x, z):
            hist = np.bincount(((v - v.min()) / 0.05).astype(np.int64))
            score += float((hist.astype(np.float64) ** 2).sum())
        if score > best_score:
            best, best_score = a, score
    return best


def solve_alignment(points: np.ndarray, cam_centers: np.ndarray | None,
                    cam_down: np.ndarray | None, camera_height: float,
                    up_hint: np.ndarray | None = None) -> dict:
    """Work out the transform from raw scan space to result space.

    With camera poses: "up" comes from how the phone was held, and the size
    from the phone's height above the floor. Without (an imported scan):
    `up_hint` is used and the size is left as it is.
    Returns dict(matrix 4x4, scale).
    """
    if cam_centers is not None and len(cam_centers):
        up = _unit(-cam_down.mean(axis=0))
        cam_level = float(np.median(cam_centers @ up))
    else:
        up = _unit(np.asarray(up_hint, np.float64))
        cam_level = None
    up, floor = find_floor(points, up, cam_level)

    scale = 1.0
    if cam_centers is not None and len(cam_centers):
        cam_above = float(np.median(cam_centers @ up)) - floor
        if cam_above > 1e-6:
            scale = camera_height / cam_above

    # Rotation taking `up` to +Y (a proper rotation, so nothing gets mirrored).
    helper = np.array([1.0, 0.0, 0.0]) if abs(up[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
    x_axis = _unit(helper - (helper @ up) * up)
    z_axis = np.cross(x_axis, up)
    R = np.stack([x_axis, up, z_axis])

    p = (points @ R.T) * scale
    p[:, 1] -= floor * scale
    body = p[(p[:, 1] > 0.3) & (p[:, 1] < 2.2)]
    if len(body) < 200:
        body = p
    angle = _square_up(body[:, [0, 2]])
    c, s = np.cos(angle), np.sin(angle)
    R_yaw = np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])
    R = R_yaw @ R

    p = (points @ R.T) * scale
    body = p[(p[:, 1] > floor * scale + 0.3) & (p[:, 1] < floor * scale + 2.2)]
    if len(body) < 200:
        body = p
    centre_x = float(np.mean(np.percentile(body[:, 0], [2, 98])))
    centre_z = float(np.mean(np.percentile(body[:, 2], [2, 98])))

    M = np.eye(4)
    M[:3, :3] = R * scale
    M[:3, 3] = [-centre_x, -floor * scale, -centre_z]
    return {"matrix": M, "scale": float(scale)}


def apply(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ M[:3, :3].T + M[:3, 3]


def rotate_dirs(M: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    R = M[:3, :3] / np.cbrt(np.linalg.det(M[:3, :3]))
    return dirs @ R.T


def camera_path(M: np.ndarray, centers: np.ndarray, forward: np.ndarray) -> list:
    """Filming path in result space as [x, z, yaw, height] rows.

    yaw follows the viewer's convention: looking along (-sin yaw, -cos yaw) in x,z.
    """
    c = apply(M, centers)
    f = rotate_dirs(M, forward)
    yaw = np.arctan2(-f[:, 0], -f[:, 2])
    return [[round(float(c[i, 0]), 3), round(float(c[i, 2]), 3), round(float(yaw[i]), 4),
             round(float(c[i, 1]), 3)] for i in range(len(c))]


def build_maps(points: np.ndarray, weights: np.ndarray, rgb: np.ndarray,
               path_xz: np.ndarray | None, out_dir: Path, dense: bool) -> dict:
    """Draw the floor plan, the top-down photo map and the collision grid.

    points are already in result space. `dense` says whether this is a full
    scan (millions of splats) or only the sparse preview points.
    Returns the map description stored in scene.json.
    """
    pts = np.asarray(points, np.float32)
    weights = np.asarray(weights, np.float32)
    keep = (pts[:, 1] > -0.4) & (pts[:, 1] < 3.5) & (weights > 0.15)
    if path_xz is not None and len(path_xz):
        lo = path_xz.min(axis=0) - 9.0
        hi = path_xz.max(axis=0) + 9.0
        keep &= (pts[:, 0] > lo[0]) & (pts[:, 0] < hi[0]) & (pts[:, 2] > lo[1]) & (pts[:, 2] < hi[1])
    pts, weights, rgb = pts[keep], weights[keep], np.asarray(rgb, np.float32)[keep]
    if len(pts) < 20:
        raise RuntimeError("The scan has too few points to draw a map.")

    x0, x1 = np.percentile(pts[:, 0], [0.5, 99.5])
    z0, z1 = np.percentile(pts[:, 2], [0.5, 99.5])
    if path_xz is not None and len(path_xz):
        x0, z0 = min(x0, path_xz[:, 0].min()), min(z0, path_xz[:, 1].min())
        x1, z1 = max(x1, path_xz[:, 0].max()), max(z1, path_xz[:, 1].max())
    margin = 0.6
    x0, x1, z0, z1 = x0 - margin, x1 + margin, z0 - margin, z1 + margin
    cell = CELL
    while max((x1 - x0), (z1 - z0)) / cell > 900:
        cell *= 1.5
    W = int(np.ceil((x1 - x0) / cell))
    H = int(np.ceil((z1 - z0) / cell))

    ix = ((pts[:, 0] - x0) / cell).astype(np.int64)
    iz = ((pts[:, 2] - z0) / cell).astype(np.int64)
    inside = (ix >= 0) & (ix < W) & (iz >= 0) & (iz < H)
    pts, weights, rgb, ix, iz = pts[inside], weights[inside], rgb[inside], ix[inside], iz[inside]
    flat = iz * W + ix
    y = pts[:, 1]

    def layer(y_lo, y_hi):
        m = (y > y_lo) & (y < y_hi)
        return np.bincount(flat[m], weights=weights[m], minlength=W * H).reshape(H, W)

    # --- collision: anything between shin and head height ---------------------
    body = layer(0.3, 1.75)
    filled = body[body > 0]
    if dense:
        threshold = max(3.0, 0.25 * float(np.median(filled))) if len(filled) else 3.0
    else:
        threshold = 1.5
    blocked = body >= threshold
    # drop lone specks (floating bits of fog are not furniture)
    padded = np.pad(blocked, 1).astype(np.int32)
    neighbours = sum(padded[1 + dz:1 + dz + H, 1 + dx:1 + dx + W]
                     for dz in (-1, 0, 1) for dx in (-1, 0, 1))
    blocked &= neighbours >= 3
    if dense:
        # close pinholes (a table top is one thin layer of splats) so solid
        # things are solid: grow by one cell, then shrink by one cell
        grown = np.pad(blocked, 1)
        grown = np.max([grown[1 + dz:1 + dz + H, 1 + dx:1 + dx + W]
                        for dz in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
        shrunk = np.pad(grown, 1, constant_values=True)
        blocked = np.min([shrunk[1 + dz:1 + dz + H, 1 + dx:1 + dx + W]
                          for dz in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
    # wherever the person filming walked is, by definition, walkable
    if path_xz is not None and len(path_xz):
        radius = int(np.ceil(0.32 / cell))
        dense_path = [path_xz[0]]
        for a, b in zip(path_xz[:-1], path_xz[1:]):
            steps = max(1, int(np.linalg.norm(b - a) / (cell * 0.75)))
            if np.linalg.norm(b - a) < 1.5:  # don't bridge jumps
                dense_path.extend(a + (b - a) * (k / steps) for k in range(1, steps + 1))
            else:
                dense_path.append(b)
        yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
        disc = (xx * xx + yy * yy) <= radius * radius
        for px, pz in dense_path:
            cx, cz = int((px - x0) / cell), int((pz - z0) / cell)
            for dz, dx in zip(yy[disc], xx[disc]):
                if 0 <= cz + dz < H and 0 <= cx + dx < W:
                    blocked[cz + dz, cx + dx] = False
    Image.fromarray((blocked * 255).astype(np.uint8), "L").save(out_dir / "collision.png")

    # --- floor plan: walls and furniture as pale lines, like a blueprint ------
    walls = layer(0.15, 2.3)
    top = np.percentile(walls[walls > 0], 97) if (walls > 0).any() else 1.0
    strength = np.clip(np.log1p(walls) / np.log1p(max(top, 1e-6)), 0.0, 1.0)
    if not dense:
        strength = np.clip(strength * 1.6, 0.0, 1.0)
    plan = np.zeros((H, W, 4), np.uint8)
    plan[..., 0], plan[..., 1], plan[..., 2] = 222, 233, 255
    plan[..., 3] = (strength ** 0.8 * 255).astype(np.uint8)
    plan_img = Image.fromarray(plan, "RGBA").resize((W * 2, H * 2), Image.BILINEAR)
    plan_img.save(out_dir / "map_plan.png")

    # --- photo map: look straight down with the ceiling lifted off -----------
    m = (y > -0.25) & (y < 2.05) & (weights > 0.3)
    order = np.argsort(y[m], kind="stable")
    photo = np.zeros((H * W, 4), np.float32)
    idx = flat[m][order]
    photo[idx, :3] = rgb[m][order]  # later (higher) points overwrite lower ones
    photo[idx, 3] = 1.0
    photo = photo.reshape(H, W, 4)
    photo_img = Image.fromarray((np.clip(photo, 0, 1) * 255).astype(np.uint8), "RGBA")
    if not dense:
        photo_img = photo_img.filter(ImageFilter.MaxFilter(3))
    photo_img.resize((W * 2, H * 2), Image.BILINEAR).save(out_dir / "map_photo.png")

    return {
        "x0": round(float(x0), 4), "z0": round(float(z0), 4), "cell": round(float(cell), 5),
        "width": W, "height": H,
        "plan": "map_plan.png", "photo": "map_photo.png", "collision": "collision.png",
        "blocked_share": round(float(blocked.mean()), 4),
    }


def pick_start(map_info: dict, out_dir: Path, path: list | None) -> dict:
    """Where the visitor first stands: the first filmed spot, or failing that
    the free cell nearest the middle of the room."""
    if path:
        return {"x": path[0][0], "z": path[0][1], "yaw": path[0][2]}
    blocked = np.asarray(Image.open(out_dir / map_info["collision"])) > 127
    H, W = blocked.shape
    zz, xx = np.nonzero(~blocked)
    if len(xx) == 0:
        return {"x": 0.0, "z": 0.0, "yaw": 0.0}
    d = (xx - W / 2) ** 2 + (zz - H / 2) ** 2
    k = int(np.argmin(d))
    return {
        "x": round(map_info["x0"] + (xx[k] + 0.5) * map_info["cell"], 3),
        "z": round(map_info["z0"] + (zz[k] + 0.5) * map_info["cell"], 3),
        "yaw": 0.0,
    }

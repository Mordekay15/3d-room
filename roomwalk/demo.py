"""A small made-up living room, so the walk-through can be tried before
scanning anything. It is drawn by code (flat coloured splats on boxes), so it
looks like a simple model - not like a real scan."""

from __future__ import annotations

import numpy as np

SPACING = 0.022
THIN = 0.003


def _rect(rng, origin, u, v, colour_fn, spacing=SPACING):
    """Splats covering the rectangle origin + a*u + b*v (a, b in 0..1).
    u and v must be axis-aligned edge vectors."""
    u, v, origin = np.asarray(u, float), np.asarray(v, float), np.asarray(origin, float)
    lu, lv = np.linalg.norm(u), np.linalg.norm(v)
    nu, nv = max(2, int(lu / spacing)), max(2, int(lv / spacing))
    a, b = np.meshgrid((np.arange(nu) + 0.5) / nu, (np.arange(nv) + 0.5) / nv)
    a = a.ravel() + rng.uniform(-0.3, 0.3, a.size) / nu
    b = b.ravel() + rng.uniform(-0.3, 0.3, b.size) / nv
    xyz = origin + a[:, None] * u + b[:, None] * v
    scales = np.full((len(xyz), 3), THIN)
    scales[:, np.argmax(np.abs(u))] = lu / nu * 0.72
    scales[:, np.argmax(np.abs(v))] = lv / nv * 0.72
    rgb = np.clip(np.asarray(colour_fn(xyz, a, b), float) * np.ones((len(xyz), 3)), 0, 1)
    return xyz, rgb, scales


def _box(rng, lo, hi, colour_fn, top_fn=None, spacing=SPACING):
    """Five visible faces of an axis-aligned box (no underside)."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    dx, dy, dz = hi - lo
    X, Y, Z = np.array([dx, 0, 0]), np.array([0, dy, 0]), np.array([0, 0, dz])
    parts = [
        _rect(rng, [lo[0], hi[1], lo[2]], X, Z, top_fn or colour_fn, spacing),
        _rect(rng, lo, X, Y, colour_fn, spacing),
        _rect(rng, [lo[0], lo[1], hi[2]], X, Y, colour_fn, spacing),
        _rect(rng, lo, Z, Y, colour_fn, spacing),
        _rect(rng, [hi[0], lo[1], lo[2]], Z, Y, colour_fn, spacing),
    ]
    return parts


def build_demo_room(seed: int = 7):
    """Returns (xyz, rgb, scales) in metres, Y up, floor at y=0, room centred."""
    rng = np.random.default_rng(seed)
    W, D, H = 6.4, 4.8, 2.7
    x0, x1, z0, z1 = -W / 2, W / 2, -D / 2, D / 2
    parts = []

    def grain(n, amount=0.03):
        return rng.normal(0, amount, (n, 1))

    def floor(xyz, a, b):
        plank = np.floor((xyz[:, 2] - z0) / 0.19)
        tone = 0.86 + 0.14 * np.sin(plank * 12.9898) * np.cos(plank * 4.1)
        seam = (np.abs(((xyz[:, 2] - z0) / 0.19) % 1.0 - 0.5) > 0.47) * 0.25
        pool = sum(0.10 * np.exp(-((xyz[:, 0] - lx) ** 2 + (xyz[:, 2] - lz) ** 2) / 0.9)
                   for lx in (-1.9, 0.0, 1.9) for lz in (-1.1, 1.1))
        base = np.array([0.70, 0.55, 0.40])
        return base * (tone - seam + pool)[:, None] + grain(len(xyz), 0.02)

    def wall(xyz, a, b):
        shade = 0.84 + 0.12 * (xyz[:, 1] / H)
        col = np.array([0.93, 0.91, 0.87]) * shade[:, None] + grain(len(xyz), 0.012)
        skirting = xyz[:, 1] < 0.09
        col[skirting] = np.array([0.98, 0.98, 0.97])
        return col

    def ceiling(xyz, a, b):
        col = np.ones((len(xyz), 3)) * np.array([0.96, 0.96, 0.95]) + grain(len(xyz), 0.008)
        for lx in (-1.9, 0.0, 1.9):
            for lz in (-1.1, 1.1):
                d = (xyz[:, 0] - lx) ** 2 + (xyz[:, 2] - lz) ** 2
                col[d < 0.0064] = np.array([1.0, 0.97, 0.86])
        return col

    parts.append(_rect(rng, [x0, 0, z0], [W, 0, 0], [0, 0, D], floor))
    parts.append(_rect(rng, [x0, H, z0], [W, 0, 0], [0, 0, D], ceiling))

    def wall_with_window(xyz, a, b):
        col = wall(xyz, a, b)
        wx, wy = xyz[:, 0], xyz[:, 1]
        frame = (np.abs(wx) < 1.36) & (wy > 0.84) & (wy < 2.26)
        glass = (np.abs(wx) < 1.30) & (wy > 0.90) & (wy < 2.20)
        col[frame] = np.array([0.20, 0.21, 0.23])
        sky = np.array([0.62, 0.78, 0.95]) + (wy[:, None] - 0.9) * np.array([-0.10, -0.06, 0.02])
        hills = wy < 1.25 + 0.12 * np.sin(wx * 2.3) + 0.05 * np.sin(wx * 7.1)
        sky[hills] = np.array([0.42, 0.55, 0.38])
        col[glass] = sky[glass]
        bars = glass & ((np.abs(wx) < 0.02) | (np.abs(wy - 1.55) < 0.02))
        col[bars] = np.array([0.20, 0.21, 0.23])
        return col

    def wall_with_art(xyz, a, b):
        col = wall(xyz, a, b)
        wz, wy = xyz[:, 2], xyz[:, 1]
        canvas = (np.abs(wz - 0.2) < 0.85) & (wy > 1.05) & (wy < 1.95)
        u, v = wz * 3.1, wy * 3.7
        r = 0.55 + 0.45 * np.sin(u * 1.7 + np.sin(v * 1.3))
        g = 0.45 + 0.35 * np.sin(v * 1.1 + 1.0 + np.cos(u * 0.9))
        bl = 0.35 + 0.30 * np.sin(u * 0.7 - v * 0.8 + 2.0)
        art = np.stack([r, g, bl], axis=1)
        col[canvas] = art[canvas]
        return col

    def wall_with_door(xyz, a, b):
        col = wall(xyz, a, b)
        wx, wy = xyz[:, 0], xyz[:, 1]
        door = (np.abs(wx - 1.9) < 0.45) & (wy < 2.05)
        col[door] = np.array([0.36, 0.26, 0.19]) + grain(int(door.sum()), 0.015)
        knob = ((wx - 1.58) ** 2 + (wy - 1.0) ** 2) < 0.0016
        col[knob] = np.array([0.85, 0.80, 0.62])
        return col

    parts.append(_rect(rng, [x0, 0, z0], [W, 0, 0], [0, H, 0], wall_with_window))
    parts.append(_rect(rng, [x0, 0, z1], [W, 0, 0], [0, H, 0], wall_with_door))
    parts.append(_rect(rng, [x0, 0, z0], [0, 0, D], [0, H, 0], wall_with_art))
    parts.append(_rect(rng, [x1, 0, z0], [0, 0, D], [0, H, 0], wall))

    def rug(xyz, a, b):
        u = (xyz[:, 0] + xyz[:, 2]) / 0.32
        v = (xyz[:, 0] - xyz[:, 2]) / 0.32
        line = (np.abs(u % 1.0 - 0.5) > 0.44) | (np.abs(v % 1.0 - 0.5) > 0.44)
        col = np.where(line[:, None], np.array([0.92, 0.93, 0.90]), np.array([0.10, 0.45, 0.47]))
        return col + grain(len(xyz), 0.02)

    parts.append(_rect(rng, [-1.7, 0.012, -0.9], [3.0, 0, 0], [0, 0, 2.1], rug))

    def cloth(base):
        base = np.array(base)
        return lambda xyz, a, b: base * (0.88 + 0.12 * (xyz[:, 1:2] / 0.9)) + grain(len(xyz), 0.02)

    def flat(base, noise=0.012):
        base = np.array(base)
        return lambda xyz, a, b: base + grain(len(xyz), noise)

    sofa = cloth([0.74, 0.36, 0.27])
    parts += _box(rng, [-1.25, 0.06, 0.95], [1.25, 0.44, 1.85], sofa)     # seat
    parts += _box(rng, [-1.25, 0.44, 1.60], [1.25, 0.86, 1.85], sofa)     # back
    parts += _box(rng, [-1.45, 0.06, 0.95], [-1.25, 0.62, 1.85], sofa)    # arms
    parts += _box(rng, [1.25, 0.06, 0.95], [1.45, 0.62, 1.85], sofa)
    cushion = cloth([0.95, 0.93, 0.88])
    for cx in (-0.8, 0.0, 0.8):
        parts += _box(rng, [cx - 0.26, 0.44, 1.40], [cx + 0.26, 0.74, 1.60], cushion)

    parts += _box(rng, [-0.6, 0.30, -0.25], [0.6, 0.38, 0.35], flat([0.20, 0.17, 0.15]))  # table
    for lx in (-0.52, 0.46):
        for lz in (-0.19, 0.25):
            parts += _box(rng, [lx, 0.0, lz], [lx + 0.06, 0.30, lz + 0.06], flat([0.14, 0.12, 0.11]))
    parts += _box(rng, [-0.2, 0.38, -0.05], [0.12, 0.42, 0.18], flat([0.90, 0.88, 0.80]))  # book

    def marble(xyz, a, b):
        vein = np.abs(np.sin(xyz[:, 0] * 9 + 3 * np.sin(xyz[:, 2] * 5))) > 0.985
        col = np.ones((len(xyz), 3)) * np.array([0.95, 0.95, 0.94])
        col[vein] = np.array([0.70, 0.70, 0.72])
        return col

    parts += _box(rng, [1.5, 0.0, -1.5], [2.9, 0.92, -0.75], flat([0.80, 0.72, 0.60]), marble)  # island
    parts += _box(rng, [-3.05, 0.0, -2.2], [-2.7, 0.45, -1.5], flat([0.25, 0.25, 0.27]))        # plant pot

    # plant: a loose ball of leaves
    n = 5000
    d = rng.normal(0, 1, (n, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    r = rng.uniform(0.15, 0.55, (n, 1))
    leaves = np.array([-2.87, 1.05, -1.85]) + d * r * np.array([1.0, 1.25, 1.0])
    leaf_rgb = np.array([0.16, 0.42, 0.20]) * rng.uniform(0.7, 1.35, (n, 1))
    leaf_scale = np.stack([rng.uniform(0.015, 0.05, n), rng.uniform(0.006, 0.012, n),
                           rng.uniform(0.015, 0.05, n)], axis=1)
    parts.append((leaves, np.clip(leaf_rgb, 0, 1), leaf_scale))

    # pendant lamp over the table
    n = 2500
    d = rng.normal(0, 1, (n, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    lamp = np.array([0.0, 1.95, 0.05]) + d * 0.17
    parts.append((lamp, np.tile([1.0, 0.95, 0.82], (n, 1)), np.full((n, 3), 0.012)))
    parts += _box(rng, [-0.006, 2.1, 0.044], [0.006, 2.7, 0.056], flat([0.1, 0.1, 0.1]), spacing=0.012)

    xyz = np.concatenate([p[0] for p in parts]).astype(np.float32)
    rgb = np.concatenate([p[1] for p in parts]).astype(np.float32)
    scales = np.concatenate([p[2] for p in parts]).astype(np.float32)
    return xyz, np.clip(rgb, 0, 1), scales

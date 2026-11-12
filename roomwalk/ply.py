"""Read and write Gaussian-splat .ply files (the standard 3DGS layout)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

SH_C0 = 0.28209479177387814

_PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}


def read_header(path: Path):
    """Return (numpy dtype of one vertex, vertex count, byte offset of data)."""
    with open(path, "rb") as f:
        head = b""
        while b"end_header" not in head:
            chunk = f.read(4096)
            if not chunk:
                raise ValueError("Not a .ply file (no header end found)")
            head += chunk
            if len(head) > 1_000_000:
                raise ValueError("Not a .ply file (header too long)")
    end = head.index(b"end_header")
    offset = head.index(b"\n", end) + 1
    lines = head[:end].decode("ascii", "replace").splitlines()
    if not lines or lines[0].strip() != "ply":
        raise ValueError("Not a .ply file")
    fmt = next((l.split()[1] for l in lines if l.startswith("format ")), "")
    if fmt == "ascii":
        raise ValueError("Text .ply files are not supported; export a binary .ply")
    endian = ">" if fmt == "binary_big_endian" else "<"
    count, fields, in_vertex = 0, [], False
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "element":
            in_vertex = parts[1] == "vertex"
            if in_vertex:
                count = int(parts[2])
            elif fields:
                break  # only the first (vertex) element is read
        elif parts[0] == "property" and in_vertex:
            if parts[1] == "list":
                raise ValueError("Unexpected list property in splat .ply")
            fields.append((parts[2], endian + _PLY_TYPES[parts[1]]))
    if not count or not fields:
        raise ValueError("The .ply file has no points in it")
    return np.dtype(fields), count, offset


def read_splats(path: Path, max_points: int | None = None):
    """Read the parts of a splat file needed for maps: centres, opacity, colour.

    Returns dict(xyz (N,3) float32, opacity (N,) 0..1, rgb (N,3) 0..1).
    Works for plain point clouds too (opacity defaults to 1).
    """
    dtype, count, offset = read_header(path)
    data = np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(count,))
    step = 1
    if max_points and count > max_points:
        step = int(np.ceil(count / max_points))
    view = data[::step]
    names = dtype.names
    xyz = np.stack([view["x"], view["y"], view["z"]], axis=1).astype(np.float32)
    if "opacity" in names:
        with np.errstate(over="ignore"):
            opacity = 1.0 / (1.0 + np.exp(-view["opacity"].astype(np.float32)))
    else:
        opacity = np.ones(len(view), np.float32)
    if "f_dc_0" in names:
        rgb = np.stack([view["f_dc_0"], view["f_dc_1"], view["f_dc_2"]], axis=1)
        rgb = 0.5 + SH_C0 * rgb.astype(np.float32)
    elif "red" in names:
        rgb = np.stack([view["red"], view["green"], view["blue"]], axis=1).astype(np.float32) / 255.0
    else:
        rgb = np.full((len(view), 3), 0.7, np.float32)
    ok = np.isfinite(xyz).all(axis=1)
    return {
        "xyz": xyz[ok],
        "opacity": opacity[ok],
        "rgb": np.clip(rgb[ok], 0.0, 1.0),
        "count": count,
    }


def write_splats(path: Path, xyz, rgb, scales, quats=None, opacity=0.95) -> None:
    """Write a splat .ply from centres, colours (0..1) and per-axis sizes.

    scales: (N,3) sizes in scene units (stored as log). quats: (N,4) w,x,y,z.
    """
    xyz = np.asarray(xyz, np.float32)
    n = len(xyz)
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2",
             "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    out = np.zeros(n, dtype=[(name, "<f4") for name in names])
    out["x"], out["y"], out["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    dc = (np.clip(np.asarray(rgb, np.float32), 0.0, 1.0) - 0.5) / SH_C0
    out["f_dc_0"], out["f_dc_1"], out["f_dc_2"] = dc[:, 0], dc[:, 1], dc[:, 2]
    op = np.clip(np.broadcast_to(np.asarray(opacity, np.float32), (n,)), 1e-4, 1 - 1e-4)
    out["opacity"] = np.log(op / (1.0 - op))
    log_scales = np.log(np.maximum(np.broadcast_to(np.asarray(scales, np.float32), (n, 3)), 1e-7))
    out["scale_0"], out["scale_1"], out["scale_2"] = log_scales[:, 0], log_scales[:, 1], log_scales[:, 2]
    if quats is None:
        out["rot_0"] = 1.0
    else:
        q = np.asarray(quats, np.float32)
        out["rot_0"], out["rot_1"], out["rot_2"], out["rot_3"] = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n
    header += "".join("property float %s\n" % name for name in names) + "end_header\n"
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        f.write(out.tobytes())

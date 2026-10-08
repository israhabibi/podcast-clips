"""Face crop tracking helpers shared by the smart renderer and tests."""

import numpy as np
from scipy.interpolate import CubicSpline


def validate_video_dimensions(width, height):
    if (isinstance(width, bool) or isinstance(height, bool)
            or not isinstance(width, int) or not isinstance(height, int)
            or width <= 0 or height <= 0):
        raise ValueError("Source video must have positive integer width and height.")
    crop_width = int(height * 9 / 16)
    if crop_width <= 0 or crop_width > width:
        raise ValueError(
            f"Source video dimensions {width}x{height} cannot fit a 9:16 crop."
        )
    return crop_width


def smooth_track(pts, dur, crop_w, w):
    """Interpolate crop-center positions and clamp every sample to valid bounds."""
    if crop_w <= 0 or w <= 0 or crop_w > w:
        raise ValueError("Crop width must fit within positive source video dimensions.")
    margin = min(60.0, max(0.0, (w - crop_w) / 2))
    min_cx = crop_w / 2 + margin
    max_cx = w - crop_w / 2 - margin
    if min_cx > max_cx:
        min_cx = max_cx = w / 2
    if not pts:
        return [(0, w / 2)]
    ts = [p[0] for p in pts]
    cxs = [min(max(p[1], min_cx), max_cx) for p in pts]
    med = []
    for i in range(len(cxs)):
        win = cxs[max(0, i - 2):i + 3]
        med.append(sorted(win)[len(win) // 2])
    steps = 90
    grid = np.linspace(0, dur, steps)
    if len(ts) >= 4:
        xs = CubicSpline(ts, med)(grid)
    else:
        xs = np.interp(grid, ts, med)
    max_v = 180.0
    for i in range(1, len(xs)):
        dt = grid[i] - grid[i - 1]
        dv = xs[i] - xs[i - 1]
        lim = max_v * dt
        if abs(dv) > lim:
            xs[i] = xs[i - 1] + np.sign(dv) * lim
    k = max(steps // 15, 5)
    smoothed = np.convolve(np.pad(xs, k // 2, mode="edge"), np.ones(k) / k, mode="valid")
    if len(smoothed) < len(xs):
        smoothed = np.interp(grid, np.linspace(0, 1, len(smoothed)), smoothed)
    clamped = np.clip(smoothed, min_cx, max_cx)
    return list(zip(grid, clamped))

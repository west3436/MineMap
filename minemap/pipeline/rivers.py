"""Step: carve real rivers into heightmap.png and write rivers_mask.png.

Channel shape: lowlands flood to a wet channel below the water level, uplands get a
dry valley whose depth tapers to zero by `carve_max_m` of real elevation. Walls are
filleted (depth grows with distance from the bank) and the outer banks are blurred.
The carve is idempotent: the first run snapshots heightmap.orig.png and every run
carves from that snapshot.
"""
from __future__ import annotations

import math
import shutil

import numpy as np
from PIL import Image, ImageDraw

from ..data.rivers import load_reaches
from ..geo import Grid, u16_to_y, y_to_u16
from ..progress import Reporter
from ..project import Project, RiverSettings, ScaleSettings

Image.MAX_IMAGE_PIXELS = None


def width_by_order(settings: RiverSettings) -> dict[int, int]:
    # JSON round trips turn the int keys into strings
    return {int(k): int(v) for k, v in settings.width_by_order.items()}


def width_for_order(order: int, settings: RiverSettings) -> int:
    order = int(order)
    table = width_by_order(settings)
    if table and order > max(table):
        return settings.width_max
    return min(table.get(order, 0), settings.width_max)


def carve_threshold_y(rs: RiverSettings, sc: ScaleSettings) -> float:
    """Terrain Y above which rivers are never carved."""
    return sc.water_level + rs.carve_max_m * sc.vertical_exaggeration / sc.meters_per_block


def carve_bottom(t, rs: RiverSettings, sc: ScaleSettings) -> np.ndarray:
    """Target channel bottom Y for river pixels with terrain heights t."""
    t = np.asarray(t, dtype=np.float32)
    T = carve_threshold_y(rs, sc)
    denom = max(T - rs.flood_threshold_y, 1e-6)
    f = np.clip((T - t) / denom, 0.0, 1.0)
    dry = np.maximum(t - rs.dry_depth * f, sc.water_level + 1.0)
    wet = np.float32(sc.water_level - rs.channel_depth)
    return np.where(t <= rs.flood_threshold_y, wet, dry).astype(np.float32)


def apply_carve(Y, river, rs: RiverSettings, sc: ScaleSettings, dist=None) -> np.ndarray:
    """Lower Y along river pixels; never raises terrain. dist tapers the walls."""
    Y = np.asarray(Y, dtype=np.float32)
    river = np.asarray(river)
    if river.dtype != bool:
        river = river > 0
    out = Y.copy()
    t = Y[river]
    bottom = carve_bottom(t, rs, sc)
    if dist is not None and rs.wall_slope > 0:
        wall = t - rs.wall_slope * np.asarray(dist)[river].astype(np.float32)
        bottom = np.maximum(bottom, wall)
    out[river] = np.minimum(t, bottom)
    return out


def rasterize_rivers(reaches, grid: Grid, rs: RiverSettings) -> np.ndarray:
    """Width map (uint16, H x W); wider rivers win where reaches overlap."""
    H, W = grid.height, grid.width
    by_width: dict[int, list] = {}
    for order, pts in reaches:
        w = width_for_order(order, rs)
        if w <= 0 or len(pts) < 2:
            continue
        xy = [grid.lonlat_to_px(lon, lat) for lon, lat in pts]
        by_width.setdefault(w, []).append(xy)
    width_px = np.zeros((H, W), dtype=np.uint16)
    for w in sorted(by_width, reverse=True):
        img = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(img)
        for xy in by_width[w]:
            d.line(xy, fill=255, width=w, joint="curve")
        m = (np.asarray(img) > 0) & (width_px == 0)
        width_px[m] = w
    return width_px


def _blur_f32(arr: np.ndarray, radius: float) -> np.ndarray:
    """Separable Gaussian blur without scipy."""
    r = max(1, int(math.ceil(radius * 3)))
    xs = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-0.5 * (xs / radius) ** 2)
    k = (k / k.sum()).astype(np.float32)
    H, W = arr.shape
    out = np.empty_like(arr)
    for i in range(H):
        out[i] = np.convolve(arr[i], k, mode="same")
    tmp = np.empty_like(arr)
    for j in range(W):
        tmp[:, j] = np.convolve(out[:, j], k, mode="same")
    return tmp


def _bank_distance(river: np.ndarray, max_d: int) -> np.ndarray:
    """Cityblock distance from each river pixel to the nearest bank, capped at max_d.
    The image edge counts as a bank."""
    river = np.asarray(river, dtype=bool)
    dist = np.zeros(river.shape, dtype=np.uint8)
    cur = river.copy()
    md = max(1, int(max_d))
    for d in range(1, md + 1):
        dist[cur] = d
        if d == md or not cur.any():
            break
        er = cur.copy()
        er[1:, :] &= cur[:-1, :]
        er[:-1, :] &= cur[1:, :]
        er[:, 1:] &= cur[:, :-1]
        er[:, :-1] &= cur[:, 1:]
        er[0, :] = er[-1, :] = False
        er[:, 0] = er[:, -1] = False
        cur = er
    return dist


def carve_heightmap(Y: np.ndarray, reaches, grid: Grid, rs: RiverSettings, sc: ScaleSettings):
    """Returns (carved Y, width map, mask) with mask 2 = flooded, 1 = dry valley, 0 = none."""
    width_px = rasterize_rivers(reaches, grid, rs)
    river = width_px > 0
    Y = np.asarray(Y, dtype=np.float32)
    dist = None
    if rs.wall_slope > 0:
        max_depth = max(rs.dry_depth, rs.flood_threshold_y - (sc.water_level - rs.channel_depth))
        max_d = int(np.ceil(max_depth / rs.wall_slope)) + 1
        dist = _bank_distance(river, max_d)
    Yc_hard = apply_carve(Y, river, rs, sc, dist)
    delta = Y - Yc_hard
    if rs.bank_blur_px > 0:
        delta = _blur_f32(delta, rs.bank_blur_px)
    Yc = np.minimum(Yc_hard, Y - np.maximum(delta, 0.0))
    mask = np.zeros(Y.shape, dtype=np.uint8)
    mask[river & (Yc < sc.water_level)] = 2
    mask[river & (Yc >= sc.water_level)] = 1
    return Yc, width_px, mask


def run(project: Project, reporter: Reporter) -> None:
    rs, sc = project.rivers, project.scale
    if not rs.enabled:
        if project.rivers_mask_png.exists():
            project.rivers_mask_png.unlink()
        if project.heightmap_orig_png.exists():
            # restore the uncarved terrain so a disabled step leaves no channels behind
            shutil.copy2(project.heightmap_orig_png, project.heightmap_png)
        reporter.log("rivers disabled")
        reporter.progress(1.0)
        return
    grid = project.grid()
    if not project.heightmap_orig_png.exists():
        shutil.copy2(project.heightmap_png, project.heightmap_orig_png)
        reporter.log("snapshot pristine heightmap -> heightmap.orig.png")
    v = np.asarray(Image.open(project.heightmap_orig_png)).astype(np.uint16)
    if v.shape != (grid.height, grid.width):
        raise RuntimeError("heightmap size does not match the project grid; rebuild the heightmap")
    Y = u16_to_y(v, sc.build_low, sc.build_high)
    reaches = load_reaches(grid.bbox, rs.min_strahler, reporter)
    reporter.check_cancel()
    Yc, _width, mask = carve_heightmap(Y, reaches, grid, rs, sc)
    Image.fromarray(y_to_u16(Yc, sc.build_low, sc.build_high), mode="I;16").save(project.heightmap_png)
    Image.fromarray(mask, mode="L").save(project.rivers_mask_png)
    nwet, ndry = int((mask == 2).sum()), int((mask == 1).sum())
    reporter.log(f"carved {nwet + ndry} px ({nwet} flooded, {ndry} dry valley)")
    reporter.progress(1.0)

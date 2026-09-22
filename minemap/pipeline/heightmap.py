"""Step: DEM -> baked heightmap.png + sea_mask.png."""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..data.dem import estimate, fetch_elevation
from ..geo import pick_zoom, y_from_elevation, y_to_u16
from ..progress import Reporter
from ..project import Project, ScaleSettings

Image.MAX_IMAGE_PIXELS = None


def deseam(Y: np.ndarray, passes: int = 2) -> np.ndarray:
    """Light vertical blur. The Terrarium mosaic stitches several source DEMs and
    their boundaries show as east-west steps; a 3 row blur turns each step into a
    ramp without touching east-west detail or peak heights."""
    Y = Y.astype(np.float32, copy=True)
    for _ in range(passes):
        sm = Y.copy()
        sm[1:-1, :] = 0.5 * Y[1:-1, :] + 0.25 * Y[:-2, :] + 0.25 * Y[2:, :]
        Y = sm
    return Y


def bake(elev_m: np.ndarray, scale: ScaleSettings) -> np.ndarray:
    """Metres -> clamped block Y (float32)."""
    Y = y_from_elevation(np.asarray(elev_m, dtype=np.float32), scale.water_level,
                         scale.vertical_exaggeration, scale.meters_per_block)
    if scale.deseam:
        Y = deseam(Y)
    return np.clip(Y, scale.y_min, scale.y_max).astype(np.float32)


def run(project: Project, reporter: Reporter) -> None:
    grid = project.grid()
    scale = project.scale
    zoom = scale.zoom_override if scale.zoom_override is not None else pick_zoom(grid.bbox, scale.meters_per_block)
    est = estimate(grid, zoom)
    reporter.log(f"heightmap grid {grid.width} x {grid.height} px, zoom {zoom}, "
                 f"{est['tiles']} tiles, mosaic {est['mosaic_mb']} MB")
    elev = fetch_elevation(grid, zoom, reporter)
    Y = bake(elev, scale)
    v = y_to_u16(Y, scale.build_low, scale.build_high)
    Image.fromarray(v, mode="I;16").save(project.heightmap_png)
    sea = (elev <= 0).astype(np.uint8) * 255
    Image.fromarray(sea, mode="L").save(project.sea_mask_png)
    if project.heightmap_orig_png.exists():
        project.heightmap_orig_png.unlink()   # stale river baseline, rivers step re-snapshots
        reporter.log("removed stale river baseline")
    cy, cx = Y.shape[0] // 2, Y.shape[1] // 2
    reporter.log(f"wrote {project.heightmap_png} ({v.shape[1]}x{v.shape[0]}, 16-bit)")
    reporter.log(f"centre Y={Y[cy, cx]:.0f}  max Y={Y.max():.0f}  min Y={Y.min():.0f}  "
                 f"sea {float(sea.mean() / 255 * 100):.1f}%")
    reporter.progress(1.0)

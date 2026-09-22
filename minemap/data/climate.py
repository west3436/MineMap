"""Koppen-Geiger climate classes from Beck et al. 2018 (1 km raster).

The GeoTIFF is 21600 x 43200 uint8, tiled PackBits, 1/120 degree per pixel, origin at
lon -180 lat 90. Decoding the whole thing needs 900 MB, so we read only the tiles
under the bbox and sample nearest neighbour onto the output grid.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import requests

from ..geo import BBox, Grid
from ..paths import cache_dir
from ..progress import Reporter

RASTER_URL = "https://ndownloader.figshare.com/files/12407516"
RASTER_NAME = "Beck_KG_V1_present_0p0083.tif"
ZIP_NAME = "Beck_KG_V1.zip"
PIXELS_PER_DEG = 120.0
RASTER_W, RASTER_H = 43200, 21600

_TABLE = json.loads((Path(__file__).parent / "kg_classes.json").read_text(encoding="utf-8"))
KG_CODES: dict[int, str] = {0: ""}
KG_CODES.update({int(k): v["code"] for k, v in _TABLE.items()})
KG_NAMES: dict[str, str] = {v["code"]: v["name"] for v in _TABLE.values()}
KG_RGB: dict[str, tuple[int, int, int]] = {v["code"]: tuple(v["rgb"]) for v in _TABLE.values()}


def kg_group(code: str) -> str:
    """Main climate group letter: "Dfb" -> "D"."""
    return code[:1] if code else ""


def window_for(bbox: BBox) -> tuple[int, int, int, int]:
    """(r0, r1, c0, c1) raster pixel window covering the bbox, half open, clamped."""
    c0 = int(np.floor((bbox.west + 180.0) * PIXELS_PER_DEG))
    c1 = int(np.ceil((bbox.east + 180.0) * PIXELS_PER_DEG))
    r0 = int(np.floor((90.0 - bbox.north) * PIXELS_PER_DEG))
    r1 = int(np.ceil((90.0 - bbox.south) * PIXELS_PER_DEG))
    c0, c1 = max(0, c0), min(RASTER_W, max(c1, c0 + 1))
    r0, r1 = max(0, r0), min(RASTER_H, max(r1, r0 + 1))
    return r0, r1, c0, c1


def ensure_raster(cache: Path | None = None, reporter: Reporter | None = None) -> Path:
    cache = cache or cache_dir("climate")
    tif = cache / RASTER_NAME
    if tif.exists():
        return tif
    zpath = cache / ZIP_NAME
    if not zpath.exists():
        if reporter:
            reporter.log("downloading Koppen-Geiger raster (71 MB, one time)")
        r = requests.get(RASTER_URL, timeout=900, stream=True)
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(zpath, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if reporter and total:
                    reporter.progress(got / total * 0.5)
    with zipfile.ZipFile(zpath) as z:
        z.extract(RASTER_NAME, cache)
    zpath.unlink(missing_ok=True)   # the other members are unused; keep the cache lean
    return tif


def read_window(tif: Path, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
    """Decode only the tiles intersecting the window; returns uint8 (r1-r0, c1-c0)."""
    import tifffile
    out = np.zeros((r1 - r0, c1 - c0), dtype=np.uint8)
    with tifffile.TiffFile(str(tif)) as t:
        p = t.pages[0]
        tw, tl = p.tilewidth, p.tilelength
        ncols = (p.imagewidth + tw - 1) // tw
        fh = t.filehandle
        for tr in range(r0 // tl, (r1 - 1) // tl + 1):
            for tc in range(c0 // tw, (c1 - 1) // tw + 1):
                idx = tr * ncols + tc
                fh.seek(p.dataoffsets[idx])
                data = fh.read(p.databytecounts[idx])
                arr, _, _ = p.decode(data, idx)
                tile = np.asarray(arr).reshape(tl, tw)
                ty, tx = tr * tl, tc * tw
                ys, ye = max(ty, r0), min(ty + tl, r1)
                xs, xe = max(tx, c0), min(tx + tw, c1)
                out[ys - r0:ye - r0, xs - c0:xe - c0] = tile[ys - ty:ye - ty, xs - tx:xe - tx]
    return out


def fetch_climate(grid: Grid, reporter: Reporter, cache: Path | None = None) -> np.ndarray:
    """uint8 KG class value per output pixel (0 = ocean or no data)."""
    tif = ensure_raster(cache, reporter)
    r0, r1, c0, c1 = window_for(grid.bbox)
    win = read_window(tif, r0, r1, c0, c1)
    reporter.progress(0.6)
    H, W = grid.height, grid.width
    lon = np.array([grid.col_lon(c) for c in range(W)], dtype=np.float64)
    cols = np.clip(np.floor((lon + 180.0) * PIXELS_PER_DEG).astype(np.int64) - c0, 0, win.shape[1] - 1)
    out = np.empty((H, W), dtype=np.uint8)
    for i in range(H):
        row = int(np.floor((90.0 - grid.row_lat(i)) * PIXELS_PER_DEG)) - r0
        row = min(max(row, 0), win.shape[0] - 1)
        out[i] = win[row, cols]
        if i % 1024 == 0:
            reporter.check_cancel()
            reporter.progress(0.6 + 0.4 * i / H)
    reporter.progress(1.0)
    return out

"""Elevation from the AWS Terrarium tile set.

Tiles are Web Mercator PNGs (256 px) whose RGB encode metres as
R * 256 + G + B / 256 - 32768. We mosaic every tile touching the bbox in memory,
then resample bilinearly onto the plate carree output grid, one row at a time.

Memory: the mosaic is float32 of (tiles_y * 256) x (tiles_x * 256), so roughly
tiles * 256 KB. A 5,000 tile region needs about 1.3 GB for the mosaic on top of
the output raster. estimate() reports both so the GUI can warn.
"""
from __future__ import annotations

import io
import math
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from ..geo import BBox, Grid, deg2tile
from ..paths import cache_dir
from ..progress import Reporter

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE = 256

_session = requests.Session()


def tile_range(bbox: BBox, zoom: int) -> tuple[int, int, int, int]:
    """Integer tile bounds (x0, x1, y0, y1), half open, covering the bbox."""
    x0f, y0f = deg2tile(bbox.west, bbox.north, zoom)
    x1f, y1f = deg2tile(bbox.east, bbox.south, zoom)
    return (int(math.floor(x0f)), int(math.ceil(x1f)),
            int(math.floor(y0f)), int(math.ceil(y1f)))


def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    """(H, W, 3) uint8 Terrarium pixels -> float32 metres."""
    a = np.asarray(rgb, dtype=np.float32)
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


def estimate(grid: Grid, zoom: int) -> dict:
    x0, x1, y0, y1 = tile_range(grid.bbox, zoom)
    tiles = (x1 - x0) * (y1 - y0)
    mosaic_mb = tiles * TILE * TILE * 4 / 1e6
    output_mb = grid.width * grid.height * 4 / 1e6
    return {"tiles": tiles, "mosaic_mb": round(mosaic_mb, 1), "output_mb": round(output_mb, 1)}


def fetch_tile(z: int, x: int, y: int, cache: Path) -> Image.Image | None:
    path = cache / f"{z}_{x}_{y}.png"
    if path.exists():
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            path.unlink(missing_ok=True)   # corrupt cache file, refetch
    url = TILE_URL.format(z=z, x=x, y=y)
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = _session.get(url, timeout=60)
            if r.status_code == 404:
                return None                # no tile over open ocean: sea level
            r.raise_for_status()
            path.write_bytes(r.content)
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:             # network hiccup, retry with backoff
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch tile {z}/{x}/{y}: {last}")


def build_mosaic(bbox: BBox, zoom: int, reporter: Reporter, cache: Path) -> tuple[np.ndarray, int, int]:
    x0, x1, y0, y1 = tile_range(bbox, zoom)
    tw, th = x1 - x0, y1 - y0
    reporter.log(f"DEM tiles: x[{x0}:{x1}] y[{y0}:{y1}] = {tw}x{th} ({tw * th} tiles) at z{zoom}")
    mosaic = np.zeros((th * TILE, tw * TILE), dtype=np.float32)
    done = 0
    total = tw * th
    for ix in range(x0, x1):
        for iy in range(y0, y1):
            reporter.check_cancel()
            img = fetch_tile(zoom, ix, iy, cache)
            if img is not None:
                ry, rx = (iy - y0) * TILE, (ix - x0) * TILE
                mosaic[ry:ry + TILE, rx:rx + TILE] = decode_terrarium(np.asarray(img, dtype=np.uint8))
            done += 1
            reporter.progress(done / total * 0.6)
    return mosaic, x0, y0


def resample(mosaic: np.ndarray, x0: int, y0: int, grid: Grid, zoom: int, reporter: Reporter) -> np.ndarray:
    """Bilinear sample of the Mercator mosaic onto the plate carree grid."""
    H, W = grid.height, grid.width
    out = np.empty((H, W), dtype=np.float32)
    Mh, Mw = mosaic.shape
    lon_arr = np.array([grid.col_lon(c) for c in range(W)], dtype=np.float64)
    txg = (lon_arr + 180.0) / 360.0 * (2 ** zoom)
    px = txg * TILE - x0 * TILE
    px0 = np.clip(np.floor(px).astype(np.int64), 0, Mw - 1)
    px1 = np.clip(px0 + 1, 0, Mw - 1)
    fx = (px - px0).astype(np.float32)
    for i in range(H):
        _, tyf = deg2tile(grid.bbox.west, grid.row_lat(i), zoom)
        py = tyf * TILE - y0 * TILE
        py0 = min(max(int(math.floor(py)), 0), Mh - 1)
        py1 = min(py0 + 1, Mh - 1)
        fy = np.float32(py - py0)
        top = mosaic[py0, px0] * (1 - fx) + mosaic[py0, px1] * fx
        bot = mosaic[py1, px0] * (1 - fx) + mosaic[py1, px1] * fx
        out[i] = top * (1 - fy) + bot * fy
        if i % 256 == 0:
            reporter.check_cancel()
            reporter.progress(0.6 + 0.4 * i / H)
    reporter.progress(1.0)
    return out


def fetch_elevation(grid: Grid, zoom: int, reporter: Reporter, cache: Path | None = None) -> np.ndarray:
    """float32 metres, shape (grid.height, grid.width)."""
    cache = cache or cache_dir("dem")
    mosaic, x0, y0 = build_mosaic(grid.bbox, zoom, reporter, cache)
    return resample(mosaic, x0, y0, grid, zoom, reporter)

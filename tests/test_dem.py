import math

import numpy as np

from minemap.data.dem import tile_range, decode_terrarium, estimate, TILE
from minemap.geo import BBox, deg2tile, make_grid


def test_tile_range_covers_bbox():
    b = BBox(45.7, 24.0, 122.8, 146.2)
    x0, x1, y0, y1 = tile_range(b, 10)
    fx0, fy0 = deg2tile(b.west, b.north, 10)
    fx1, fy1 = deg2tile(b.east, b.south, 10)
    assert x0 == math.floor(fx0) and y0 == math.floor(fy0)
    assert x1 == math.ceil(fx1) and y1 == math.ceil(fy1)
    assert x0 < x1 and y0 < y1
    assert (x1 - x0) * (y1 - y0) > 4000


def test_tile_range_single_tile():
    x0, x1, y0, y1 = tile_range(BBox(0.01, 0.0, 0.0, 0.01), 5)
    assert (x1 - x0, y1 - y0) == (1, 1)
    assert x0 == 16 and y0 == 15      # lat 0.01 is just above the equator tile boundary


def test_decode_terrarium():
    px = np.zeros((1, 3, 3), dtype=np.uint8)
    px[0, 0] = (128, 0, 0)        # 32768 - 32768 = 0 m
    px[0, 1] = (142, 192, 0)      # 142*256 + 192 - 32768 = 3776 m
    px[0, 2] = (127, 255, 128)    # -1 + 0.5 = -0.5 m
    m = decode_terrarium(px)
    assert m.dtype == np.float32
    assert np.allclose(m[0], [0.0, 3776.0, -0.5])


def test_estimate():
    g = make_grid(BBox(36.0, 35.0, 138.0, 139.0), 100.0)
    e = estimate(g, 10)
    x0, x1, y0, y1 = tile_range(g.bbox, 10)
    assert e["tiles"] == (x1 - x0) * (y1 - y0)
    assert abs(e["mosaic_mb"] - e["tiles"] * TILE * TILE * 4 / 1e6) < 0.1
    assert abs(e["output_mb"] - g.width * g.height * 4 / 1e6) < 0.1

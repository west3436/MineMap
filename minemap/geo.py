"""Geometry shared by every step: bounding box, output grid, pixel and world coordinates.

Conventions (identical to the WorldPainter export):
  * The output raster has one pixel per block. Row 0 is the north edge, column 0 the
    west edge. Columns are linear in longitude, rows linear in latitude (plate carree).
  * WorldPainter puts the raster's NW corner at world (SHIFT_X, SHIFT_Z). MineMap
    centres the map on the origin, so SHIFT_X = -(W // 2) and SHIFT_Z = -(H // 2).
  * World X grows east, world Z grows south.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

KM_PER_DEG = 111.32


@dataclass(frozen=True)
class BBox:
    north: float
    south: float
    west: float
    east: float

    def validate(self) -> None:
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("latitude bounds must satisfy -90 <= south < north <= 90")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("longitude bounds must satisfy -180 <= west < east <= 180")

    @property
    def mid_lat(self) -> float:
        return (self.north + self.south) / 2.0

    @property
    def mid_lon(self) -> float:
        return (self.east + self.west) / 2.0

    def intersects(self, other: "BBox") -> bool:
        return not (other.east < self.west or other.west > self.east
                    or other.north < self.south or other.south > self.north)


@dataclass(frozen=True)
class Grid:
    """The one-pixel-per-block output raster for a bbox and horizontal scale."""
    bbox: BBox
    meters_per_block: float
    width: int
    height: int

    @property
    def shift_x(self) -> int:
        return -(self.width // 2)

    @property
    def shift_z(self) -> int:
        return -(self.height // 2)

    @property
    def border_radius_x(self) -> int:
        return math.ceil(self.width / 2)

    @property
    def border_radius_z(self) -> int:
        return math.ceil(self.height / 2)

    def lonlat_to_px(self, lon: float, lat: float) -> tuple[float, float]:
        """Geographic -> fractional (col, row)."""
        b = self.bbox
        col = (lon - b.west) / (b.east - b.west) * self.width
        row = (b.north - lat) / (b.north - b.south) * self.height
        return col, row

    def px_to_lonlat(self, col: float, row: float) -> tuple[float, float]:
        b = self.bbox
        lon = b.west + col / self.width * (b.east - b.west)
        lat = b.north - row / self.height * (b.north - b.south)
        return lon, lat

    def px_to_world(self, col: float, row: float) -> tuple[int, int]:
        return int(math.floor(col)) + self.shift_x, int(math.floor(row)) + self.shift_z

    def world_to_px(self, x: int, z: int) -> tuple[int, int]:
        return x - self.shift_x, z - self.shift_z

    def lonlat_to_world(self, lon: float, lat: float) -> tuple[int, int]:
        c, r = self.lonlat_to_px(lon, lat)
        return self.px_to_world(c, r)

    def row_lat(self, row: int) -> float:
        return self.bbox.north - (row + 0.5) / self.height * (self.bbox.north - self.bbox.south)

    def col_lon(self, col: int) -> float:
        return self.bbox.west + (col + 0.5) / self.width * (self.bbox.east - self.bbox.west)


def make_grid(bbox: BBox, meters_per_block: float) -> Grid:
    bbox.validate()
    if meters_per_block <= 0:
        raise ValueError("meters_per_block must be positive")
    ns_km = (bbox.north - bbox.south) * KM_PER_DEG
    ew_km = (bbox.east - bbox.west) * KM_PER_DEG * math.cos(math.radians(bbox.mid_lat))
    h = max(1, int(round(ns_km * 1000.0 / meters_per_block)))
    w = max(1, int(round(ew_km * 1000.0 / meters_per_block)))
    return Grid(bbox, meters_per_block, w, h)


def deg2tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Web Mercator fractional tile coordinates."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def pick_zoom(bbox: BBox, meters_per_block: float, max_zoom: int = 15) -> int:
    """Smallest Terrarium zoom whose ground resolution at the bbox centre is finer
    than the requested block size (so the resample never upsamples)."""
    res0 = 156543.03 * math.cos(math.radians(bbox.mid_lat))  # m/px at z0
    z = 0
    while z < max_zoom and res0 / (2 ** z) > meters_per_block:
        z += 1
    return z


def tile_count(bbox: BBox, zoom: int) -> int:
    x0, y0 = deg2tile(bbox.west, bbox.north, zoom)
    x1, y1 = deg2tile(bbox.east, bbox.south, zoom)
    return (math.ceil(x1) - math.floor(x0)) * (math.ceil(y1) - math.floor(y0))


def y_from_elevation(elev_m, water_level: int, vertical_exaggeration: float, meters_per_block: float):
    """Elevation in metres -> block Y. One block spans meters_per_block/vertical_exaggeration metres."""
    return water_level + elev_m * (vertical_exaggeration / meters_per_block)


def elevation_from_y(y, water_level: int, vertical_exaggeration: float, meters_per_block: float):
    return (y - water_level) * (meters_per_block / vertical_exaggeration)


def y_to_u16(y, build_low: int, build_high: int):
    """Block Y -> 16-bit heightmap sample (numpy or scalar)."""
    import numpy as np
    span = float(build_high - build_low)
    return np.clip(np.round((np.asarray(y, dtype=np.float32) - build_low) / span * 65535.0), 0, 65535).astype(np.uint16)


def u16_to_y(v, build_low: int, build_high: int):
    import numpy as np
    span = float(build_high - build_low)
    return build_low + np.asarray(v).astype(np.float32) / 65535.0 * span

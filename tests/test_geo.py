import numpy as np
import pytest

from minemap.geo import BBox, make_grid, pick_zoom, y_to_u16, u16_to_y, y_from_elevation, elevation_from_y


def test_make_grid_sizes():
    g = make_grid(BBox(45.7, 24.0, 122.8, 146.2), 100.0)
    assert g.height == round((45.7 - 24.0) * 111.32 * 10)
    assert 21000 < g.width < 22000
    assert g.shift_x == -(g.width // 2) and g.shift_z == -(g.height // 2)
    assert g.border_radius_x >= g.width / 2 and g.border_radius_z >= g.height / 2


def test_grid_scale_halves():
    b = BBox(36.0, 35.0, 138.0, 139.0)
    g1, g2 = make_grid(b, 100.0), make_grid(b, 200.0)
    assert abs(g1.width / g2.width - 2) < 0.02
    assert abs(g1.height / g2.height - 2) < 0.02


def test_bbox_validation():
    with pytest.raises(ValueError):
        make_grid(BBox(35.0, 36.0, 138.0, 139.0), 100.0)
    with pytest.raises(ValueError):
        make_grid(BBox(36.0, 35.0, 138.0, 139.0), 0)


def test_round_trips():
    g = make_grid(BBox(36.0, 35.0, 138.0, 139.0), 100.0)
    c, r = g.lonlat_to_px(138.5, 35.5)
    assert abs(c - g.width / 2) < 1e-6 and abs(r - g.height / 2) < 1e-6
    lon, lat = g.px_to_lonlat(c, r)
    assert abs(lon - 138.5) < 1e-9 and abs(lat - 35.5) < 1e-9
    x, z = g.px_to_world(0, 0)
    assert (x, z) == (g.shift_x, g.shift_z)
    assert g.world_to_px(x, z) == (0, 0)
    cx, cz = g.lonlat_to_world(138.5, 35.5)
    assert abs(cx) <= 1 and abs(cz) <= 1
    assert g.bbox.north > g.row_lat(0) > g.row_lat(g.height - 1) > g.bbox.south
    assert g.bbox.west < g.col_lon(0) < g.col_lon(g.width - 1) < g.bbox.east


def test_pick_zoom_monotonic():
    b = BBox(36.0, 35.0, 138.0, 139.0)
    zs = [pick_zoom(b, m) for m in (25, 50, 100, 200, 400, 800)]
    assert zs == sorted(zs, reverse=True)
    assert pick_zoom(b, 100) == 10   # z10 is 124 m/px at lat 35.5, the nearest level
    assert pick_zoom(b, 60) == 11


def test_y_u16_round_trip():
    y = np.array([-64, 0, 62, 200, 320], dtype=np.float32)
    v = y_to_u16(y, -64, 320)
    assert v.dtype == np.uint16 and v[0] == 0 and v[-1] == 65535
    back = u16_to_y(v, -64, 320)
    assert np.allclose(back, y, atol=0.01)


def test_elevation_y_round_trip():
    y = y_from_elevation(3776.0, 62, 4.0, 100.0)
    assert abs(y - (62 + 3776 * 0.04)) < 1e-6
    assert abs(elevation_from_y(y, 62, 4.0, 100.0) - 3776.0) < 1e-6

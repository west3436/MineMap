import numpy as np

from minemap.data.rivers import regions_for, keep_reach
from minemap.geo import BBox, make_grid
from minemap.pipeline.rivers import (carve_bottom, apply_carve, rasterize_rivers, _bank_distance,
                                     carve_heightmap, carve_threshold_y, width_for_order)
from minemap.project import RiverSettings, ScaleSettings

RS = RiverSettings()
SC = ScaleSettings()


def test_carve_bottom_lowland_floods():
    b = carve_bottom(np.array([60.0, 64.0]), RS, SC)
    assert np.all(b == SC.water_level - RS.channel_depth)


def test_carve_bottom_upland_tapers():
    T = carve_threshold_y(RS, SC)
    mid = (RS.flood_threshold_y + T) / 2
    b = carve_bottom(np.array([70.0, mid, T, T + 50]), RS, SC)
    assert 70 - RS.dry_depth <= b[0] < 70
    assert abs((mid - b[1]) - RS.dry_depth * 0.5) < 1e-3
    assert b[2] == T and b[3] == T + 50           # at and above the carve max: untouched
    assert np.all(b >= SC.water_level + 1)


def test_apply_carve_never_raises():
    Y = np.full((5, 5), 80.0, np.float32)         # below the carve ceiling (94 at defaults)
    river = np.zeros((5, 5), bool)
    river[2, :] = True
    out = apply_carve(Y, river, RS, SC)
    assert np.all(out <= Y)
    assert np.all(out[2] < 80) and np.all(out[[0, 1, 3, 4]] == 80)
    dist = np.zeros((5, 5), np.uint8)
    dist[2, :] = 1
    out2 = apply_carve(Y, river, RS, SC, dist)
    assert np.all(out2[2] == 79)                  # wall slope 1 at distance 1
    high = np.full((3, 3), 200.0, np.float32)     # above the carve ceiling: untouched
    assert np.all(apply_carve(high, np.ones((3, 3), bool), RS, SC) == 200)


def test_width_for_order_and_string_keys():
    rs = RiverSettings(width_by_order={"4": 4, "5": 7, "6": 10})
    assert width_for_order(3, rs) == 0 and width_for_order(5, rs) == 7 and width_for_order(9, rs) == rs.width_max


def test_rasterize_widths():
    g = make_grid(BBox(36.0, 35.0, 138.0, 139.0), 1000.0)   # about 111 x 91 px
    lat = 35.5
    reaches = [(4, [(138.0, lat), (139.0, lat)]), (7, [(138.5, 35.0), (138.5, 36.0)])]
    w = rasterize_rivers(reaches, g, RS)
    assert w.max() == RS.width_max
    col, row = (int(v) for v in g.lonlat_to_px(138.5, lat))
    assert w[row, col] == RS.width_max                  # wide river wins at the crossing
    assert w[row, 5] == 4
    assert int((w[:, 5] > 0).sum()) in (4, 5)


def test_bank_distance():
    river = np.zeros((7, 7), bool)
    river[1:6, 1:6] = True
    d = _bank_distance(river, 5)
    assert d[1, 1] == 1 and d[3, 3] == 3 and d[0, 0] == 0
    edge = np.ones((3, 3), bool)
    de = _bank_distance(edge, 5)
    assert de[0, 0] == 1 and de[1, 1] == 2           # image edge counts as bank


def test_carve_heightmap_mask():
    g = make_grid(BBox(36.0, 35.0, 138.0, 139.0), 1000.0)
    Y = np.full((g.height, g.width), 63.0, np.float32)
    Y[:, : g.width // 2] = 90.0
    reaches = [(6, [(138.0, 35.5), (139.0, 35.5)])]
    Yc, w, mask = carve_heightmap(Y, reaches, g, RS, SC)
    assert np.all(Yc <= Y)
    assert (mask == 2).any() and (mask == 1).any()
    assert np.all(mask[w == 0] == 0)


def test_regions_for():
    assert regions_for(BBox(45.7, 24.0, 122.8, 146.2)) == ["as", "si"]
    assert "eu" in regions_for(BBox(52.0, 48.0, -5.0, 8.0))
    assert "na" in regions_for(BBox(40.0, 35.0, -120.0, -110.0))
    assert regions_for(BBox(-70.0, -80.0, 0.0, 10.0)) == []


def test_keep_reach():
    b = BBox(36.0, 35.0, 138.0, 139.0)
    assert keep_reach(4, (138.2, 35.2, 138.4, 35.4), b, 4)
    assert not keep_reach(3, (138.2, 35.2, 138.4, 35.4), b, 4)
    assert not keep_reach(5, (140.0, 35.2, 141.0, 35.4), b, 4)

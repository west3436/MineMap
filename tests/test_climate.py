from minemap.data.climate import KG_CODES, KG_NAMES, KG_RGB, kg_group, window_for, RASTER_W, RASTER_H
from minemap.geo import BBox


def test_kg_codes_complete():
    assert set(KG_CODES) == set(range(31))
    assert KG_CODES[0] == "" and KG_CODES[1] == "Af" and KG_CODES[14] == "Cfa" and KG_CODES[30] == "EF"
    assert len(KG_NAMES) == 30 and len(KG_RGB) == 30
    assert KG_RGB["Dfb"] == (55, 200, 255)


def test_kg_group():
    assert kg_group("Dfb") == "D"
    assert kg_group("ET") == "E"
    assert kg_group("") == ""


def test_window_for():
    r0, r1, c0, c1 = window_for(BBox(45.7, 24.0, 122.8, 146.2))
    assert (r0, c0) == (int((90 - 45.7) * 120), int((122.8 + 180) * 120))
    assert r1 >= (90 - 24.0) * 120 and c1 >= (146.2 + 180) * 120
    assert 0 <= r0 < r1 <= RASTER_H and 0 <= c0 < c1 <= RASTER_W


def test_window_clamped_at_edges():
    r0, r1, c0, c1 = window_for(BBox(90.0, 80.0, -180.0, -170.0))
    assert r0 == 0 and c0 == 0 and r1 <= RASTER_H and c1 <= RASTER_W
    r0, r1, c0, c1 = window_for(BBox(-80.0, -90.0, 170.0, 180.0))
    assert r1 == RASTER_H and c1 == RASTER_W

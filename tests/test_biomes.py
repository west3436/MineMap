import json

import numpy as np
import pytest
from PIL import Image

from minemap import geo
from minemap.pipeline import biomes as bm
from minemap.pipeline.defaults import default_biome_settings
from minemap.progress import NullReporter
from minemap.project import BiomeSettings, ElevationBand, Project, WeightedBiome


def _settings(**kw):
    base = dict(
        rules={"Cfb": [WeightedBiome(biome="minecraft:forest", weight=0.5),
                       WeightedBiome(biome="minecraft:plains", weight=0.5)]},
        elevation_bands=[ElevationBand(min_m=2000, biome="minecraft:jagged_peaks", climates=["C"])],
        river_biome="minecraft:river", fill_unknown="minecraft:savanna", noise_cell=4, seed=1,
    )
    base.update(kw)
    return BiomeSettings(**base)


def test_build_palette_vanilla_passthrough():
    pal, remap = bm.build_palette(_settings())
    assert pal[0] is None
    assert set(pal[1:]) == {"minecraft:forest", "minecraft:plains", "minecraft:jagged_peaks",
                            "minecraft:river", "minecraft:savanna"}
    assert remap == {}


def test_build_palette_placeholders_for_modded():
    s = _settings(rules={"Af": [WeightedBiome(biome="mymod:glow_forest"), WeightedBiome(biome="minecraft:jungle")]})
    pal, remap = bm.build_palette(s)
    assert len(remap) == 1
    ph, target = next(iter(remap.items()))
    assert target == "mymod:glow_forest"
    assert ph in bm.VANILLA_OVERWORLD_BIOMES and ph not in bm.AUTO_BIOMES
    assert ph not in {"minecraft:jungle", "minecraft:river", "minecraft:savanna", "minecraft:jagged_peaks"}
    assert ph in pal


def test_build_palette_exhaustion():
    many = [WeightedBiome(biome=f"mod:b{i}") for i in range(60)]
    with pytest.raises(ValueError):
        bm.build_palette(_settings(rules={"Af": many}))


def test_default_settings_have_all_codes_and_build():
    s = default_biome_settings()
    assert len(s.rules) == 30
    pal, remap = bm.build_palette(s)
    assert remap == {}
    assert all(p in bm.VANILLA_OVERWORLD_BIOMES for p in pal[1:])


def _index_of(s):
    pal, remap = bm.build_palette(s)
    return bm.index_map(s, pal, remap), pal


def test_classify_weighted_pick_and_bands():
    s = _settings()
    index_of, pal = _index_of(s)
    # KG 15 = Cfb
    climate = np.full((2, 4), 15, dtype=np.uint8)
    elev = np.zeros((2, 4), dtype=np.float32)
    elev[1, :] = 2500
    noise = np.array([[0.1, 0.4, 0.6, 0.9]] * 2, dtype=np.float32)
    land = np.ones((2, 4), dtype=bool)
    out = bm.classify(climate, elev, noise, land, s, index_of)
    f, p, j = index_of["minecraft:forest"], index_of["minecraft:plains"], index_of["minecraft:jagged_peaks"]
    assert out[0].tolist() == [f, f, p, p]
    assert (out[1] == j).all()


def test_classify_unknown_code_uses_fill_and_water_stays_zero():
    s = _settings()
    index_of, _ = _index_of(s)
    climate = np.array([[4, 15]], dtype=np.uint8)   # BWh has no rule here
    elev = np.zeros((1, 2), dtype=np.float32)
    noise = np.zeros((1, 2), dtype=np.float32)
    land = np.array([[True, False]])
    out = bm.classify(climate, elev, noise, land, s, index_of)
    assert out[0, 0] == index_of["minecraft:savanna"]
    assert out[0, 1] == 0


def test_band_climate_filter_by_group_and_code():
    band_c = ElevationBand(min_m=0, biome="x", climates=["C"])
    band_code = ElevationBand(min_m=0, biome="x", climates=["Dfb"])
    band_all = ElevationBand(min_m=0, biome="x")
    assert bm._band_matches(band_c, "Cfb") and not bm._band_matches(band_c, "Dfb")
    assert bm._band_matches(band_code, "Dfb") and not bm._band_matches(band_code, "Dfa")
    assert bm._band_matches(band_all, "EF")


def test_river_overlay_and_interior():
    out = np.ones((3, 3), dtype=np.uint8)
    rmask = np.zeros((3, 3), dtype=np.uint8)
    rmask[1, 1] = 2
    rmask[0, 0] = 1
    o = bm.apply_river_overlay(out, rmask, 7)
    assert o[1, 1] == 7 and o[0, 0] == 1
    land = np.ones((3, 3), dtype=bool)
    inner = bm.interior_mask(land)
    assert inner.sum() == 1 and inner[1, 1]


def test_run_writes_outputs(tmp_path):
    proj = Project(name="t", workspace=str(tmp_path))
    proj.bbox.north, proj.bbox.south, proj.bbox.west, proj.bbox.east = 35.1, 35.0, 138.0, 138.1
    proj.scale.meters_per_block = 1000
    proj.biomes = _settings()
    g = proj.grid()
    H, W = g.height, g.width
    Y = np.full((H, W), 70, dtype=np.float32)
    Image.fromarray(geo.y_to_u16(Y, -64, 320)).save(proj.heightmap_png)
    sea = np.zeros((H, W), dtype=np.uint8)
    sea[:, 0] = 255
    Image.fromarray(sea).save(proj.sea_mask_png)
    Image.fromarray(np.full((H, W), 15, dtype=np.uint8)).save(proj.climate_png)
    bm.run(proj, NullReporter())
    assert proj.biomes_png.exists() and proj.biomes_palette_json.exists()
    pal = json.loads(proj.biomes_palette_json.read_text())
    assert "1" in pal
    out = np.asarray(Image.open(proj.biomes_png))
    assert out.shape == (H, W)
    assert (out[:, 0] == 0).all()           # sea stays unpainted
    assert (proj.preview_dir / "biomes.png").exists()
    assert (proj.preview_dir / "heightmap.png").exists()
    meta = json.loads((proj.preview_dir / "meta.json").read_text())
    assert meta["bbox"]["north"] == 35.1

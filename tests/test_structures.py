import json
from types import SimpleNamespace

import numpy as np
from PIL import Image

from minemap import geo
from minemap.pipeline import structures as st
from minemap.pipeline.vanilla_structures import DEFAULT_ENTRIES, REFERENCE_AREA
from minemap.progress import NullReporter
from minemap.project import Project, StructureEntry


def _scan():
    return SimpleNamespace(
        biome_tags={"#mod:has_structure/tower": ["mod:glow_forest", "minecraft:forest"],
                    "#minecraft:is_ocean": ["minecraft:ocean", "minecraft:deep_ocean"]},
        structures={
            "mod:tower": SimpleNamespace(biomes="#mod:has_structure/tower", start_height={"absolute": 0},
                                         projection="WORLD_SURFACE_WG", step="surface_structures",
                                         raw={"type": "minecraft:jigsaw", "start_height": {"absolute": 0},
                                              "project_start_to_heightmap": "WORLD_SURFACE_WG"}),
            "mod:wreck": SimpleNamespace(biomes="#minecraft:is_ocean", start_height={"absolute": 0},
                                         projection=None, step="surface_structures", raw={}),
        },
        structure_sets={},
    )


def _rasters():
    # 6x6 down-sampled grid: left third water, middle forest (idx 1), right glow_forest (idx 2)
    B = np.zeros((6, 6), dtype=np.uint8)
    B[:, 2:4] = 1
    B[:, 4:] = 2
    Hm = np.full((6, 6), int(geo.y_to_u16(np.float32(80), -64, 320)), dtype=np.uint16)
    return st.Rasters(B, Hm, {1: "minecraft:forest", 2: "mod:glow_forest"}, stride=1, height=6, width=6)


def test_resolve_mask_classes():
    r = _rasters()
    water, coast = st.make_water_masks(r.B_ds)
    deep = st.deep_water_mask(water)
    scan = _scan()
    e_ocean = StructureEntry(id="x", placement="ocean")
    assert (st.resolve_mask(e_ocean, None, scan, r.final_names, r.B_ds, water, deep, coast) == water).all()
    e_coast = StructureEntry(id="x", placement="coast")
    m = st.resolve_mask(e_coast, None, scan, r.final_names, r.B_ds, water, deep, coast)
    assert m.any() and (m[:, 2]).all() and not m[:, 3].any()
    # auto via tags: forest + glow_forest columns
    e_auto = StructureEntry(id="mod:tower")
    m = st.resolve_mask(e_auto, scan.structures["mod:tower"], scan, r.final_names, r.B_ds, water, deep, coast)
    assert m[:, 2:].all() and not m[:, :2].any()
    # auto via tags resolving to ocean biomes -> water
    e_w = StructureEntry(id="mod:wreck")
    m = st.resolve_mask(e_w, scan.structures["mod:wreck"], scan, r.final_names, r.B_ds, water, deep, coast)
    assert (m == water).all()
    # explicit biome list wins
    e_b = StructureEntry(id="mod:tower", biomes=["mod:glow_forest"])
    m = st.resolve_mask(e_b, scan.structures["mod:tower"], scan, r.final_names, r.B_ds, water, deep, coast)
    assert m[:, 4:].all() and not m[:, :4].any()


def test_effective_count_scales_with_area():
    e = StructureEntry(id="x", count=40)
    assert st.effective_count(e, 1.0, REFERENCE_AREA) == 40
    assert st.effective_count(e, 0.5, REFERENCE_AREA) == 20
    assert st.effective_count(e, 1.0, REFERENCE_AREA // 4) == 10
    assert st.effective_count(e, 1.0, 100) == 1
    e.enabled = False
    assert st.effective_count(e, 1.0, REFERENCE_AREA) == 0


def test_classify_ymode_and_override():
    surf = SimpleNamespace(start_height={"absolute": 3}, projection="WORLD_SURFACE_WG")
    assert st.classify_ymode(surf) == ("surface", 3)
    ocean = SimpleNamespace(start_height={"absolute": 0}, projection="OCEAN_FLOOR")
    assert st.classify_ymode(ocean) == ("ocean_floor", 0)
    plain = SimpleNamespace(start_height={"absolute": 0}, projection=None)
    assert st.classify_ymode(plain) == ("plain", 0)
    uni = SimpleNamespace(start_height={"type": "minecraft:uniform", "min_inclusive": {"absolute": -40},
                                        "max_inclusive": {"absolute": 20}}, projection="WORLD_SURFACE")
    assert st.classify_ymode(uni) == ("surface", -10)
    o = st.make_override({"a": 1, "project_start_to_heightmap": "WORLD_SURFACE"}, 77)
    assert o == {"a": 1, "start_height": {"absolute": 77}}


def test_build_placements_overrides_and_skips():
    r = _rasters()
    grid = geo.make_grid(geo.BBox(35.1, 35.0, 138.0, 138.1), 100)
    entries = [StructureEntry(id="mod:tower", count=REFERENCE_AREA // 36 * 4),   # wants 4 on a 36 px map
               StructureEntry(id="mod:missing", count=5),
               StructureEntry(id="minecraft:shipwreck", count=REFERENCE_AREA // 36 * 2, placement="ocean")]
    placements, overrides, log = st.build_placements(entries, _scan(), r, grid, 1.0, 1, "ns")
    ids = {p[0] for p in placements}
    assert any(i.startswith("ns:tower_y") for i in ids)
    assert "minecraft:shipwreck" in ids
    assert len(overrides) >= 1
    oid = next(iter(overrides))
    assert overrides[oid]["start_height"] == {"absolute": 80}
    assert "project_start_to_heightmap" not in overrides[oid]
    skipped = [l for l in log if l[0] == "mod:missing"]
    assert skipped and skipped[0][1] == "skipped"
    for _pid, x, z, _l in placements:
        assert grid.shift_x <= x < grid.shift_x + 6 and grid.shift_z <= z < grid.shift_z + 6


def test_deconflict_spreads():
    items = [("a", 0, 0, True), ("b", 0, 0, True), ("c", 10, 10, True)]
    out = st.deconflict(items, sep=80)
    pts = [(x, z) for _s, x, z, _e in out]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            assert abs(pts[i][0] - pts[j][0]) >= 80 or abs(pts[i][1] - pts[j][1]) >= 80


def test_write_datapack_and_commands(tmp_path):
    placements = [(f"minecraft:igloo", i * 100, 0, False) for i in range(7)] + [("ns:big_y80", 500, 500, True)]
    overrides = {"big_y80": {"start_height": {"absolute": 80}}}
    waves = st.write_datapack(tmp_path / "dp", "ns", placements, overrides, batch=3, waves=2)
    assert waves == 2
    fn = tmp_path / "dp" / "data" / "ns" / "function"
    for name in ("run", "auto", "next", "reset", "test", "testplace", "wave1", "wave2", "b00000", "p00000"):
        assert (fn / f"{name}.mcfunction").exists(), name
    assert (tmp_path / "dp" / "data" / "ns" / "worldgen" / "structure" / "big_y80.json").exists()
    assert (tmp_path / "dp" / "README.txt").exists()
    p0 = (fn / "p00000.mcfunction").read_text()
    assert "place structure minecraft:igloo" in p0
    assert "[MineMap]" in (fn / "run.mcfunction").read_text()
    meta = json.loads((tmp_path / "dp" / "pack.mcmeta").read_text())
    assert meta["pack"]["pack_format"] == 48

    proj = Project(name="p", workspace=str(tmp_path))
    path = st.write_commands(proj, "ns", 8, 2)
    txt = path.read_text()
    g = proj.grid()
    assert f"/chunky radius {g.border_radius_x} {g.border_radius_z}" in txt
    assert "/chunky start" in txt and "/function ns:run" in txt


def test_run_end_to_end(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    proj.bbox.north, proj.bbox.south, proj.bbox.west, proj.bbox.east = 35.1, 35.0, 138.0, 138.1
    proj.scale.meters_per_block = 200
    proj.world.output_dir = str(tmp_path / "saves")
    proj.world_dir().mkdir(parents=True)
    proj.structures.entries = [e.model_copy() for e in DEFAULT_ENTRIES]
    proj.structures.density = 50.0
    proj.structures.stride = 2
    g = proj.grid()
    H, W = g.height, g.width
    B = np.zeros((H, W), dtype=np.uint8)
    B[:, W // 3:] = 1
    Image.fromarray(B).save(proj.biomes_png)
    proj.biomes_palette_json.write_text(json.dumps({"1": "minecraft:plains"}))
    Y = np.full((H, W), 75, dtype=np.float32)
    Image.fromarray(geo.y_to_u16(Y, -64, 320)).save(proj.heightmap_png)
    st.run(proj, NullReporter())
    dp = proj.world_dir() / "datapacks" / proj.structures.namespace
    assert (dp / "pack.mcmeta").exists()
    assert (proj.out_dir / "commands.txt").exists()
    run_txt = (dp / "data" / proj.structures.namespace / "function" / "run.mcfunction").read_text()
    assert "wave1" in run_txt

import io
import json
import zipfile
from pathlib import Path

import pytest

from minemap.instance import scan, vanilla
from minemap.instance.detect import InstanceInfo


def _jar(path: Path, files: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content if isinstance(content, (bytes, str)) else json.dumps(content))
    return path


def _j(obj) -> str:
    return json.dumps(obj)


MOD_FILES = {
    "fabric.mod.json": _j({"id": "mymod", "name": "My Mod", "version": "1.2.3"}),
    "data/mymod/worldgen/biome/misty_forest.json": _j({"has_precipitation": True, "temperature": 0.6}),
    "data/mymod/worldgen/biome/ash_wastes.json": _j({"has_precipitation": False, "temperature": 2.0}),
    "data/mymod/tags/worldgen/biome/has_structure/tower.json": _j({"values": ["mymod:misty_forest", "#minecraft:is_forest"]}),
    "data/minecraft/tags/worldgen/biome/is_nether.json": _j({"replace": False, "values": ["mymod:ash_wastes"]}),
    # comments and a trailing comma, as several mods ship them
    "data/mymod/worldgen/structure/tower.json": (
        '{\n  // a tower\n  "type": "minecraft:jigsaw", /* jigsaw */\n  "biomes": "#mymod:has_structure/tower",\n'
        '  "step": "surface_structures",\n  "start_height": {"absolute": 0},\n'
        '  "project_start_to_heightmap": "WORLD_SURFACE_WG",\n  "terrain_adaptation": "beard_thin",\n}\n'),
    "data/mymod/worldgen/structure_set/towers.json": _j({
        "structures": [{"structure": "mymod:tower", "weight": 3}],
        "placement": {"type": "minecraft:random_spread", "spacing": 40, "separation": 10, "frequency": 0.5, "salt": 1}}),
    "data/mymod/worldgen/configured_feature/ore_tin.json": _j({
        "type": "minecraft:ore", "config": {"size": 8, "targets": [
            {"state": {"Name": "mymod:tin_ore"}}, {"state": {"Name": "mymod:deepslate_tin_ore"}}]}}),
    "data/mymod/worldgen/placed_feature/ore_tin_upper.json": _j({
        "feature": "mymod:ore_tin", "placement": [{"type": "minecraft:count", "count": 6}]}),
    "data/mymod/worldgen/placed_feature/ore_tin_lower.json": _j({
        "feature": "mymod:ore_tin", "placement": [{"type": "minecraft:count", "count": 4}]}),
    "data/mymod/worldgen/configured_feature/not_an_ore.json": _j({"type": "minecraft:tree", "config": {}}),
}

VANILLA_FILES = {
    "data/minecraft/worldgen/biome/forest.json": _j({"has_precipitation": True, "temperature": 0.7}),
    "data/minecraft/worldgen/biome/plains.json": _j({"has_precipitation": True, "temperature": 0.8}),
    "data/minecraft/worldgen/biome/nether_wastes.json": _j({"has_precipitation": False, "temperature": 2.0}),
    "data/minecraft/tags/worldgen/biome/is_forest.json": _j({"values": ["minecraft:forest"]}),
    "data/minecraft/tags/worldgen/biome/is_nether.json": _j({"values": ["minecraft:nether_wastes"]}),
}


@pytest.fixture
def instance(tmp_path):
    game = tmp_path / "inst" / ".minecraft"
    _jar(game / "mods" / "mymod-1.2.3.jar", MOD_FILES)
    vjar = _jar(tmp_path / "libs" / "minecraft-1.21.1-client.jar", VANILLA_FILES)
    return InstanceInfo("prism", "Test", str(game), "neoforge", "1.21.1", str(vjar))


def test_scan_counts_and_mod_metadata(instance):
    res = scan.scan_instance(instance)
    assert [(m.id, m.name, m.version) for m in res.mods] == [("mymod", "My Mod", "1.2.3")]
    assert "mymod:misty_forest" in res.biomes
    assert res.biomes["mymod:misty_forest"].source == "mymod"
    assert res.biomes["minecraft:forest"].source == "minecraft"
    # bundled fallback fills vanilla ids the fake jar did not carry
    assert res.biomes["minecraft:cherry_grove"].source == scan.VANILLA_BUNDLED
    assert len(res.biomes) >= len(vanilla.BIOMES) + 2
    assert "mymod:tower" in res.structures
    assert "mymod:towers" in res.structure_sets
    assert "minecraft:villages" in res.structure_sets


def test_lenient_json_structure_parsed(instance):
    res = scan.scan_instance(instance)
    sd = res.structures["mymod:tower"]
    assert sd.step == "surface_structures"
    assert sd.biomes == ["#mymod:has_structure/tower"]
    assert sd.projection == "WORLD_SURFACE_WG"
    assert sd.start_height == {"absolute": 0}
    assert sd.terrain_adaptation == "beard_thin"
    assert sd.raw["type"] == "minecraft:jigsaw"


def test_tag_resolution_is_recursive_and_merges_sources(instance):
    res = scan.scan_instance(instance)
    assert res.biome_tags["#mymod:has_structure/tower"] == ["mymod:misty_forest", "minecraft:forest"]
    # the mod appended to is_nether without replace, and the vanilla jar contributed its own
    assert set(res.biome_tags["#minecraft:is_nether"]) >= {"mymod:ash_wastes", "minecraft:nether_wastes"}


def test_overworld_biomes_excludes_nether_and_end_by_tag(instance):
    res = scan.scan_instance(instance)
    ow = res.overworld_biomes()
    assert "mymod:misty_forest" in ow
    assert "mymod:ash_wastes" not in ow
    assert "minecraft:nether_wastes" not in ow
    assert "minecraft:the_end" not in ow
    assert "minecraft:forest" in ow


def test_structure_set_fields_and_kind(instance):
    res = scan.scan_instance(instance)
    ss = res.structure_sets["mymod:towers"]
    assert ss.structures == [("mymod:tower", 3)]
    assert (ss.spacing, ss.separation, ss.frequency) == (40, 10, 0.5)
    assert ss.kind == "overworld"
    assert res.structure_sets["minecraft:nether_complexes"].kind == "nether"
    assert res.structure_sets["minecraft:end_cities"].kind == "end"
    assert res.structure_sets["minecraft:villages"].kind == "overworld"


def test_ores_extracted_with_summed_count(instance):
    res = scan.scan_instance(instance)
    ids = [o.id for o in res.ores]
    assert ids == ["mymod:ore_tin"]
    o = res.ores[0]
    assert o.blocks == ["mymod:tin_ore", "mymod:deepslate_tin_ore"]
    assert o.count_per_chunk == 10.0


def test_json_round_trip(instance):
    res = scan.scan_instance(instance)
    d = json.loads(json.dumps(res.to_json()))
    back = scan.ScanResult.from_json(d)
    assert back.to_json() == res.to_json()
    assert back.structure_sets["mymod:towers"].structures == [("mymod:tower", 3)]
    assert back.overworld_biomes() == res.overworld_biomes()


def test_nested_jarjar_and_toml_metadata(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", 'modLoader="javafml"\n[[mods]]\nmodId="innerlib"\ndisplayName="Inner Lib"\nversion="0.1"\n')
        z.writestr("data/innerlib/worldgen/biome/hidden.json", _j({"temperature": 0.3}))
    game = tmp_path / "g"
    _jar(game / "mods" / "outer.jar", {
        # broken TOML (unescaped backslash) must fall back to the regex reader
        "META-INF/mods.toml": 'modLoader="javafml"\n[[mods]]\nmodId="outer"\ndisplayName="Outer \\q"\nversion="2.0"\n',
        "META-INF/jarjar/inner.jar": inner.getvalue(),
    })
    res = scan.scan_instance(InstanceInfo("folder", "g", str(game), "neoforge", "1.21.1", ""))
    ids = {m.id: m for m in res.mods}
    assert ids["outer"].version == "2.0"
    assert ids["innerlib"].name == "Inner Lib"
    assert "innerlib:hidden" in res.biomes
    assert any("bundled" in w for w in res.warnings)


def test_missing_mods_dir_warns_but_scans_vanilla(tmp_path):
    game = tmp_path / "empty"
    game.mkdir()
    res = scan.scan_instance(InstanceInfo("folder", "e", str(game), "", "", ""))
    assert res.mods == []
    assert any("no mod jars" in w for w in res.warnings)
    assert "minecraft:plains" in res.overworld_biomes()
    assert "minecraft:village_plains" in res.structures


def test_loads_lenient_variants():
    assert scan.loads_lenient(b'{"a": 1,}') == {"a": 1}
    assert scan.loads_lenient('// top\n{"a": [1, 2,], /* x */ "b": "http://x"}') == {"a": [1, 2], "b": "http://x"}
    assert scan.loads_lenient(b'\xef\xbb\xbf{"bom": true}') == {"bom": True}

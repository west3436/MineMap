import json

from minemap.pipeline import void_pack
from minemap.progress import NullReporter
from minemap.project import Project


def test_pack_format_table():
    assert void_pack.pack_format_for("1.21.1") == 48
    assert void_pack.pack_format_for("1.20.6") == 41
    assert void_pack.pack_format_for("1.21.4") == 61
    assert void_pack.pack_format_for("") == void_pack.DEFAULT_PACK_FORMAT
    assert void_pack.pack_format_for("1.21.9") == 81       # newer than the table: highest known
    assert void_pack.pack_format_for("weird") == void_pack.DEFAULT_PACK_FORMAT


def test_overworld_sets_from_scan_and_vanilla():
    scan = {"structure_sets": {
        "minecraft:villages": {"kind": "overworld"},
        "minecraft:end_cities": {"kind": "end"},
        "mod:towers": {"kind": "unknown"},
    }}
    assert void_pack.overworld_structure_sets(scan) == ["minecraft:villages", "mod:towers"]
    vanilla = void_pack.overworld_structure_sets(None)
    assert "minecraft:villages" in vanilla
    assert "minecraft:end_cities" not in vanilla
    assert "minecraft:nether_complexes" not in vanilla


def test_write_pack(tmp_path):
    n = void_pack.write_pack(tmp_path / "pk", 48, ["minecraft:villages", "mod:towers"])
    assert n == 2
    meta = json.loads((tmp_path / "pk" / "pack.mcmeta").read_text())
    assert meta["pack"]["pack_format"] == 48
    ns = json.loads((tmp_path / "pk" / "data" / "minecraft" / "worldgen" / "noise_settings" / "overworld.json").read_text())
    assert ns["noise_router"]["final_density"] == -1
    assert (tmp_path / "pk" / "data" / "mod" / "worldgen" / "structure_set" / "towers.json").exists()
    assert json.loads((tmp_path / "pk" / "data" / "minecraft" / "worldgen" / "structure_set" / "villages.json").read_text())["structures"] == []


def test_run_respects_toggle(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    proj.world.output_dir = str(tmp_path / "saves")
    proj.world_dir().mkdir(parents=True)
    proj.instance.mc_version = "1.21.1"
    void_pack.run(proj, NullReporter())
    dest = proj.world_dir() / "datapacks" / void_pack.PACK_NAME
    assert (dest / "pack.mcmeta").exists()
    proj.world.void_outside = False
    void_pack.run(proj, NullReporter())
    assert not dest.exists()

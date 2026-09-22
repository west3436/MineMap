import nbtlib
import numpy as np
from nbtlib.tag import Compound, Double, Int, String
from PIL import Image

from minemap import geo
from minemap.pipeline import level_meta
from minemap.progress import NullReporter
from minemap.project import Project


def _make_level(path):
    root = nbtlib.File({"Data": Compound({
        "SpawnX": Int(0), "SpawnY": Int(64), "SpawnZ": Int(0),
        "BorderSize": Double(60000000.0),
        "WorldGenSettings": Compound({"dimensions": Compound({
            "minecraft:overworld": Compound({"type": String("minecraft:overworld"),
                                             "generator": Compound({"type": String("minecraft:flat")})})})}),
    })}, gzipped=True)
    root.save(str(path))


def test_apply_sets_spawn_border_and_generator(tmp_path):
    lv = tmp_path / "level.dat"
    _make_level(lv)
    level_meta.apply(lv, (10, 70, -5), 1234.0, "noise")
    d = nbtlib.load(str(lv))["Data"]
    assert (int(d["SpawnX"]), int(d["SpawnY"]), int(d["SpawnZ"])) == (10, 70, -5)
    assert float(d["BorderSize"]) == 1234.0
    gen = d["WorldGenSettings"]["dimensions"]["minecraft:overworld"]["generator"]
    assert str(gen["type"]) == "minecraft:noise"


def test_run_uses_heightmap_for_spawn_y(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    proj.bbox.north, proj.bbox.south, proj.bbox.west, proj.bbox.east = 35.1, 35.0, 138.0, 138.1
    proj.scale.meters_per_block = 1000
    proj.world.output_dir = str(tmp_path / "saves")
    g = proj.grid()
    Y = np.full((g.height, g.width), 100, dtype=np.float32)
    Image.fromarray(geo.y_to_u16(Y, -64, 320)).save(proj.heightmap_png)
    wd = proj.world_dir()
    wd.mkdir(parents=True)
    _make_level(wd / "level.dat")
    level_meta.run(proj, NullReporter())
    assert (wd / "level.dat.wpbak").exists()
    d = nbtlib.load(str(wd / "level.dat"))["Data"]
    assert int(d["SpawnY"]) == 102
    assert float(d["BorderSize"]) == 2 * max(g.border_radius_x, g.border_radius_z) + 64


def test_spawn_from_latlon(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    proj.bbox.north, proj.bbox.south, proj.bbox.west, proj.bbox.east = 36.0, 35.0, 138.0, 139.0
    proj.scale.meters_per_block = 1000
    proj.world.spawn_lat, proj.world.spawn_lon = 36.0, 138.0   # NW corner
    x, y, z = level_meta.spawn_for(proj)
    g = proj.grid()
    assert (x, z) == (g.shift_x, g.shift_z)
    assert y == proj.world.spawn_y

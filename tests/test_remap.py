import json

import nbtlib
import pytest
from nbtlib.tag import Compound, List, String

from minemap.pipeline import remap
from minemap.progress import NullReporter
from minemap.project import Project


def _chunk(palette):
    return nbtlib.File({
        "sections": List[Compound]([Compound({
            "biomes": Compound({"palette": List[String]([String(p) for p in palette])}),
        })]),
    })


def _write_region(path, palettes):
    chunks = {i: remap.encode_chunk(_chunk(p)) for i, p in enumerate(palettes)}
    remap.write_region(path, chunks, None)


def test_swap_and_verify(tmp_path):
    rdir = tmp_path / "region"
    rdir.mkdir()
    _write_region(rdir / "r.0.0.mca", [["minecraft:dark_forest", "minecraft:plains"], ["minecraft:plains"]])
    mapping = {"minecraft:dark_forest": "mymod:glow_forest"}
    n = remap.swap_region_biomes(rdir, mapping)
    assert n == 1
    counts = remap.verify_region_biomes(rdir, mapping)
    assert counts == {"mymod:glow_forest": 1}
    # round trip: the unrelated palette entry survived
    roots = [r for _i, r, _c in remap.read_chunks(rdir / "r.0.0.mca")]
    assert str(roots[0]["sections"][0]["biomes"]["palette"][1]) == "minecraft:plains"


def test_guard_rejects_bad_mapping():
    with pytest.raises(ValueError):
        remap.check_remap({"mymod:a": "mymod:b"})
    with pytest.raises(ValueError):
        remap.check_remap({"minecraft:a": "minecraft:b"})


def test_run_noop_without_modded(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    proj.biomes_remap_json.write_text("{}")
    remap.run(proj, NullReporter())
    assert (proj.out_dir / remap.DONE_MARKER).exists()


def test_run_swaps_world(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    proj.world.output_dir = str(tmp_path / "saves")
    rdir = proj.world_dir() / "region"
    rdir.mkdir(parents=True)
    _write_region(rdir / "r.0.0.mca", [["minecraft:ice_spikes"]])
    proj.biomes_remap_json.write_text(json.dumps({"minecraft:ice_spikes": "mod:hot"}))
    remap.run(proj, NullReporter())
    assert json.loads((proj.out_dir / remap.DONE_MARKER).read_text())["swapped"] == 1

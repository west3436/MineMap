from minemap.pipeline import worldpainter as wp
from minemap.project import Project


def test_render_script_mentions_every_env_var(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    js = wp.render_script(proj)
    for name in ("MM_HEIGHTMAP", "MM_BIOMES", "MM_PALETTE", "MM_SAVES", "MM_WORLD", "MM_SHIFTX",
                 "MM_SHIFTY", "MM_WATER", "MM_LOW", "MM_HIGH", "MM_FORMAT", "MM_CAVES_LEVEL",
                 "MM_CAVE_MIN", "MM_CAVE_MAX", "MM_CAVE_WATER", "MM_CAVE_SURFACE"):
        assert name in js
    assert "setPopulate(true)" in js
    assert "setLayerSettings(Resources, null)" in js


def test_build_env_uses_grid_shift(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    env = wp.build_env(proj)
    g = proj.grid()
    assert env["MM_SHIFTX"] == str(g.shift_x)
    assert env["MM_SHIFTY"] == str(g.shift_z)
    assert env["MM_WORLD"] == proj.world.name
    assert env["MM_LOW"] == "-64" and env["MM_HIGH"] == "320"


def test_ensure_vmoptions_replaces_xmx(tmp_path):
    exe = tmp_path / "wpscript.exe"
    exe.write_bytes(b"")
    vm = tmp_path / "wpscript.vmoptions"
    vm.write_text("-Dfoo=bar\n-Xmx2g\n", encoding="utf-8")
    wp.ensure_vmoptions(str(exe), 10)
    lines = vm.read_text().splitlines()
    assert "-Dfoo=bar" in lines
    assert lines.count("-Xmx10g") == 1
    assert "-Xmx2g" not in lines


def test_ensure_vmoptions_creates_file(tmp_path):
    exe = tmp_path / "wpscript.exe"
    exe.write_bytes(b"")
    wp.ensure_vmoptions(str(exe), 4)
    assert (tmp_path / "wpscript.vmoptions").read_text().strip() == "-Xmx4g"


def test_find_wpscript_explicit(tmp_path):
    exe = tmp_path / "wpscript.exe"
    exe.write_bytes(b"")
    assert wp.find_wpscript(str(exe)) == str(exe)
    assert wp.find_wpscript(str(tmp_path)) == str(exe)
    assert wp.find_wpscript(str(tmp_path / "missing")) is None

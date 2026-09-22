import pytest

from minemap.pipeline import runner
from minemap.progress import Reporter
from minemap.project import Project


def test_step_order():
    assert runner.STEP_KEYS == ["climate", "heightmap", "rivers", "biomes", "export", "level",
                                "remap", "void", "structures"]


def test_run_steps_runs_in_pipeline_order(monkeypatch, tmp_path):
    order = []
    for key, label, _fn in list(runner.STEPS):
        idx = runner.STEP_KEYS.index(key)
        runner.STEPS[idx] = (key, label, (lambda k: (lambda p, r: order.append(k)))(key))
    proj = Project(name="p", workspace=str(tmp_path))
    logs = []
    runner.run_steps(proj, ["biomes", "climate", "void"], Reporter(on_log=logs.append))
    assert order == ["climate", "biomes", "void"]
    assert any("Climate raster" in l for l in logs)
    with pytest.raises(KeyError):
        runner.run_steps(proj, ["nope"], Reporter(on_log=logs.append))


def test_step_status_reflects_files(tmp_path):
    proj = Project(name="p", workspace=str(tmp_path))
    proj.world.output_dir = str(tmp_path / "saves")
    status = runner.step_status(proj)
    assert not status["climate"] and not status["export"] and not status["structures"]
    proj.climate_png.write_bytes(b"x")
    proj.rivers.enabled = False
    proj.heightmap_png.write_bytes(b"x")
    proj.sea_mask_png.write_bytes(b"x")
    wd = proj.world_dir()
    (wd / "datapacks" / proj.structures.namespace).mkdir(parents=True)
    (wd / "level.dat").write_bytes(b"x")
    status = runner.step_status(proj)
    assert status["climate"] and status["heightmap"] and status["rivers"]
    assert status["export"] and status["structures"] and not status["level"]

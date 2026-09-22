"""Server endpoint tests. Everything runs in-process with TestClient; pipeline steps
and instance detection are monkeypatched so no network or launcher is needed."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from minemap import paths
from minemap.project import Project
from minemap.server import AppState, create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(paths, "default_projects_dir", lambda: _mk(tmp_path / "projects"))
    proj = Project(name="t", workspace=str(tmp_path / "projects" / "t"))
    proj.bbox.north, proj.bbox.south, proj.bbox.west, proj.bbox.east = 35.4, 35.3, 138.6, 138.8
    state = AppState(project=proj, settings={})
    state.set_project(proj)
    app = create_app(state)
    with TestClient(app) as c:
        c.state_obj = state
        yield c


def _mk(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_get_and_put_project_updates_estimate(client):
    r = client.get("/api/project")
    assert r.status_code == 200
    body = r.json()
    assert body["project"]["name"] == "t"
    est1 = body["estimate"]
    assert est1["ok"] and est1["width"] > 0

    proj = body["project"]
    proj["bbox"]["east"] = 139.2
    r = client.put("/api/project", json=proj)
    assert r.status_code == 200
    est2 = r.json()["estimate"]
    assert est2["width"] > est1["width"]
    saved = json.loads(Path(proj["workspace"], "project.json").read_text())
    assert saved["bbox"]["east"] == 139.2


def test_put_rejects_bad_project(client):
    r = client.put("/api/project", json={"bbox": {"north": "x"}})
    assert r.status_code == 422


def test_new_and_open_project(client, tmp_path):
    r = client.post("/api/project/new", json={"name": "Alps", "dir": str(tmp_path / "pj")})
    assert r.status_code == 200
    p = r.json()["project"]
    assert p["name"] == "Alps"
    assert Path(p["workspace"], "project.json").exists()
    assert p["biomes"]["rules"], "new projects carry default biome rules"

    r = client.post("/api/project/open", json={"path": str(tmp_path / "projects" / "t")})
    assert r.status_code == 200
    assert r.json()["project"]["name"] == "t"

    r = client.post("/api/project/open", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 404


def test_estimate(client):
    e = client.get("/api/estimate").json()
    assert e["ok"]
    assert e["tiles"] >= 1 and e["zoom"] >= 0
    assert e["shift_x"] == -(e["width"] // 2)


def test_job_runs_fake_step(client, monkeypatch):
    from minemap.pipeline import runner

    def fake(project, reporter):
        reporter.log("hello from fake")
        reporter.progress(0.5)
        reporter.progress(1.0)

    monkeypatch.setattr(runner, "STEPS", [("fake", "Fake step", fake)])
    r = client.post("/api/job/start", json={"steps": ["fake"], "overwrite_world": True})
    assert r.status_code == 200
    for _ in range(100):
        j = client.get("/api/job").json()
        if j["job"] and not j["job"]["running"]:
            break
        time.sleep(0.02)
    assert not j["job"]["running"]
    assert j["job"]["steps"]["fake"]["state"] == "done"
    assert any("hello from fake" in line for line in j["log"])
    assert not (client.state_obj.project.out_dir / ".overwrite_ok").exists()

    r = client.post("/api/job/start", json={"steps": ["unknown"]})
    assert r.status_code == 422


def test_job_error_is_reported(client, monkeypatch):
    from minemap.pipeline import runner

    def boom(project, reporter):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(runner, "STEPS", [("boom", "Boom", boom), ("after", "After", lambda p, r: None)])
    client.post("/api/job/start", json={"steps": ["boom", "after"]})
    for _ in range(100):
        j = client.get("/api/job").json()
        if j["job"] and not j["job"]["running"]:
            break
        time.sleep(0.02)
    assert "kaboom" in j["job"]["error"]
    assert j["job"]["steps"]["boom"]["state"] == "error"
    assert j["job"]["steps"]["after"]["state"] == "pending"


def test_events_stream_yields_hello(client):
    with client.stream("GET", "/api/job/events?limit=1") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for chunk in r.iter_text():
            if chunk:
                assert "hello" in chunk
                break


def test_instances_monkeypatched(client, monkeypatch):
    from minemap.instance import detect

    class Info:
        def __init__(self):
            self.__dict__.update(launcher="prism", name="X", game_dir="C:/x/.minecraft",
                                 loader="neoforge", mc_version="1.21.1", vanilla_jar="")

    monkeypatch.setattr(detect, "detect_instances", lambda: [Info()])
    r = client.get("/api/instances")
    assert r.status_code == 200
    rows = r.json()["instances"]
    assert rows and rows[0]["launcher"] == "prism"


def test_biomes_catalog_and_defaults(client):
    r = client.get("/api/biomes/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "minecraft:forest" in body["vanilla"]
    assert body["kg"]["Dfb"]["name"]
    r = client.get("/api/biomes/defaults")
    assert r.status_code == 200 and r.json()["biomes"]["rules"]


def test_structures_catalog(client):
    r = client.get("/api/structures/catalog")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["structures"]]
    assert "minecraft:village_plains" in ids


def test_preview_and_commands_404_before_build(client):
    assert client.get("/api/preview/meta").status_code == 404
    assert client.get("/api/preview/biomes.png").status_code == 404
    assert client.get("/api/commands").status_code == 404
    assert client.get("/api/preview/..%2Fx.png").status_code in (400, 404)


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "MineMap" in r.text
    assert client.get("/static/app.js").status_code == 200

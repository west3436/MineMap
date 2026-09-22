"""FastAPI application: one in-memory Project, one job at a time, an SSE log stream.

Every heavy import (scan, pipeline steps) happens inside the endpoint that needs it,
so the server still starts when a sibling module is broken and tests can patch them.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import geo, paths
from .progress import Cancelled, Reporter
from .project import Project, new_project

MAX_LOG_LINES = 500
HEARTBEAT_S = 15.0


# --------------------------------------------------------------------------- settings
def load_settings() -> dict:
    p = paths.settings_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def save_settings(data: dict) -> None:
    paths.settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- job
class Job:
    """One background run of ordered steps. Status is a plain dict the UI polls."""

    def __init__(self, state: "AppState", steps: list[tuple[str, str, Any]], overwrite_world: bool):
        self.state = state
        self.steps = steps
        self.overwrite_world = overwrite_world
        self.status: dict[str, Any] = {
            "running": True,
            "step": "",
            "label": "",
            "progress": 0.0,
            "steps": {k: {"label": label, "state": "pending"} for k, label, _ in steps},
            "error": None,
            "started": time.time(),
            "finished": None,
        }
        self.reporter = Reporter(on_log=self._on_log, on_progress=self._on_progress)
        self.thread = threading.Thread(target=self._run, name="minemap-job", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _on_log(self, msg: str) -> None:
        self.state.log.append(msg)
        self.state.publish({"type": "log", "text": msg, "step": self.status["step"]})

    def _on_progress(self, step: str, frac: float) -> None:
        self.status["progress"] = frac
        self.state.publish({"type": "progress", "step": self.status["step"], "progress": frac})

    def _run(self) -> None:
        project = self.state.project
        marker = project.out_dir / ".overwrite_ok"
        try:
            if self.overwrite_world:
                marker.write_text("ok", encoding="utf-8")
            for key, label, fn in self.steps:
                self.status["step"] = key
                self.status["label"] = label
                self.status["steps"][key]["state"] = "running"
                self.status["progress"] = 0.0
                self.reporter.set_step(key)
                self.state.publish({"type": "step", "step": key, "state": "running"})
                self.reporter.log(f"== {label}")
                try:
                    fn(project, self.reporter)
                except Cancelled:
                    self.status["steps"][key]["state"] = "cancelled"
                    self.state.publish({"type": "step", "step": key, "state": "cancelled"})
                    self.reporter.log("cancelled")
                    break
                except Exception as e:  # noqa: BLE001, surfaced to the UI as the job error
                    self.status["steps"][key]["state"] = "error"
                    self.status["error"] = f"{label}: {e}"
                    self.state.publish({"type": "step", "step": key, "state": "error"})
                    self.reporter.log(f"ERROR in {label}: {e}")
                    break
                self.status["steps"][key]["state"] = "done"
                self.status["progress"] = 1.0
                self.state.publish({"type": "step", "step": key, "state": "done"})
                if self.reporter.cancelled:
                    break
        finally:
            if marker.exists():
                try:
                    marker.unlink()
                except OSError:
                    pass
            self.status["running"] = False
            self.status["finished"] = time.time()
            kind = "error" if self.status["error"] else "done"
            self.state.publish({"type": kind, "error": self.status["error"]})


# --------------------------------------------------------------------------- state
class AppState:
    def __init__(self, project: Project | None = None, settings: dict | None = None):
        self.settings = settings if settings is not None else load_settings()
        self.project = project or self._initial_project()
        self.scan: Any = None
        self.job: Job | None = None
        self.log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self.subscribers: list[queue.Queue] = []
        self.lock = threading.Lock()
        self._load_scan()

    def _initial_project(self) -> Project:
        last = self.settings.get("last_project")
        if last and Path(last).exists():
            try:
                return Project.load(Path(last))
            except (OSError, ValueError):
                pass
        return new_project("New map", paths.default_projects_dir() / "New map")

    def _load_scan(self) -> None:
        p = self.project.scan_json
        if not p.exists():
            self.scan = None
            return
        try:
            from .instance.scan import ScanResult
            self.scan = ScanResult.from_json(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001, a stale or unreadable scan is simply absent
            self.scan = None

    def set_project(self, project: Project) -> None:
        self.project = project
        project.save()
        self.settings["last_project"] = str(project.project_file)
        save_settings(self.settings)
        self._load_scan()

    def publish(self, event: dict) -> None:
        with self.lock:
            for q in list(self.subscribers):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def job_running(self) -> bool:
        return self.job is not None and self.job.status["running"]

    def start_job(self, steps: list[tuple[str, str, Any]], overwrite_world: bool = False) -> Job:
        if self.job_running():
            raise HTTPException(409, "a job is already running")
        self.job = Job(self, steps, overwrite_world)
        self.job.start()
        return self.job


# --------------------------------------------------------------------------- helpers
def sse(event: dict) -> str:
    """One server-sent event frame."""
    return "data: " + json.dumps(event) + "\n\n"


def estimate_for(project: Project) -> dict:
    try:
        grid = project.grid()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    bbox = grid.bbox
    zoom = project.scale.zoom_override if project.scale.zoom_override is not None \
        else geo.pick_zoom(bbox, project.scale.meters_per_block)
    tiles = geo.tile_count(bbox, zoom)
    px = grid.width * grid.height
    return {
        "ok": True,
        "width": grid.width,
        "height": grid.height,
        "pixels": px,
        "zoom": zoom,
        "tiles": tiles,
        "shift_x": grid.shift_x,
        "shift_z": grid.shift_z,
        "radius_x": grid.border_radius_x,
        "radius_z": grid.border_radius_z,
        "export_gb": round(px * 0.000000017, 2),
        "ram_gb": round(tiles * 256 * 256 * 4 / 1e9 + px * 4 / 1e9, 2),
    }


def _pick_via_subprocess(kind: str, initial: str) -> str:
    """Run the picker in a child process. Frozen builds re-launch the exe with the
    hidden --pick flag; source runs use the interpreter with the same flag."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--pick", kind, "--initial", initial]
    else:
        cmd = [sys.executable, "-m", "minemap", "--pick", kind, "--initial", initial]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    return out.stdout.strip()


def _pick_in_thread(kind: str, initial: str) -> str:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "dir":
            p = filedialog.askdirectory(initialdir=initial or None)
        else:
            p = filedialog.askopenfilename(initialdir=initial or None)
    finally:
        root.destroy()
    return p or ""


def pick_path(kind: str, initial: str = "") -> str:
    """Native picker. A subprocess keeps tkinter off the server thread; the frozen
    exe has no python interpreter to spawn, so it falls back to an in-thread dialog."""
    try:
        return _pick_via_subprocess(kind, initial)
    except (OSError, subprocess.SubprocessError):
        return _pick_in_thread(kind, initial)


def dir_size(p: Path) -> int:
    total = 0
    if not p.exists():
        return 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def java_info() -> dict:
    exe = shutil.which("java")
    if not exe:
        return {"found": False, "path": "", "version": ""}
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20)
        first = (out.stderr or out.stdout).strip().splitlines()[0] if (out.stderr or out.stdout) else ""
    except (OSError, subprocess.SubprocessError):
        first = ""
    return {"found": True, "path": exe, "version": first}


def suggested_placement(sdef: Any, biome_tags: dict[str, list[str]]) -> str:
    ids: set[str] = set()
    for b in getattr(sdef, "biomes", []) or []:
        if b.startswith("#"):
            ids.update(biome_tags.get(b, []))
        else:
            ids.add(b)
    if ids and all("ocean" in i for i in ids):
        return "ocean_deep" if all(i.split(":")[-1].startswith("deep_") for i in ids) else "ocean"
    if any("beach" in i or "shore" in i for i in ids):
        return "coast"
    return "land"


# --------------------------------------------------------------------------- request models
class NewProjectBody(BaseModel):
    name: str
    dir: str | None = None


class OpenProjectBody(BaseModel):
    path: str


class PickBody(BaseModel):
    kind: str = "dir"
    initial: str = ""


class SelectInstanceBody(BaseModel):
    path: str


class JobStartBody(BaseModel):
    steps: list[str]
    overwrite_world: bool = False


# --------------------------------------------------------------------------- app
def create_app(state: AppState | None = None) -> FastAPI:
    state = state or AppState()
    app = FastAPI(title="MineMap", docs_url=None, redoc_url=None)
    app.state.mm = state
    web_dir = paths.resource_dir() / "web"

    # ---- pages ---------------------------------------------------------------
    @app.get("/")
    def index():
        f = web_dir / "index.html"
        if not f.exists():
            raise HTTPException(404, "web assets missing")
        return FileResponse(f, media_type="text/html")

    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    # ---- project -------------------------------------------------------------
    def project_payload() -> dict:
        return {"project": state.project.model_dump(mode="json"),
                "estimate": estimate_for(state.project)}

    @app.get("/api/project")
    def get_project():
        return project_payload()

    @app.put("/api/project")
    def put_project(body: dict):
        if state.job_running():
            raise HTTPException(409, "cannot edit the project while a job runs")
        try:
            proj = Project.model_validate(body)
        except Exception as e:  # noqa: BLE001, validation detail goes back to the UI
            raise HTTPException(422, str(e))
        if not proj.workspace:
            proj.workspace = state.project.workspace
        state.set_project(proj)
        return project_payload()

    @app.post("/api/project/new")
    def project_new(body: NewProjectBody):
        if state.job_running():
            raise HTTPException(409, "a job is running")
        name = body.name.strip() or "New map"
        base = Path(body.dir) if body.dir else paths.default_projects_dir()
        ws = base / name
        proj = new_project(name, ws)
        # keep the instance and tool settings the user already set up
        proj.instance = state.project.instance.model_copy()
        proj.tools = state.project.tools.model_copy()
        state.set_project(proj)
        return project_payload()

    @app.post("/api/project/open")
    def project_open(body: OpenProjectBody):
        if state.job_running():
            raise HTTPException(409, "a job is running")
        p = Path(body.path)
        if p.is_dir():
            p = p / "project.json"
        if not p.exists():
            raise HTTPException(404, f"no project at {p}")
        try:
            proj = Project.load(p)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"could not load project: {e}")
        state.set_project(proj)
        return project_payload()

    @app.get("/api/projects")
    def projects():
        out = []
        base = paths.default_projects_dir()
        for d in sorted(base.iterdir()) if base.exists() else []:
            f = d / "project.json"
            if f.exists():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    out.append({"name": data.get("name", d.name), "path": str(f),
                                "modified": f.stat().st_mtime})
                except (OSError, ValueError):
                    continue
        return {"projects": out, "current": str(state.project.project_file)}

    @app.get("/api/estimate")
    def estimate():
        return estimate_for(state.project)

    @app.post("/api/pick")
    def pick(body: PickBody):
        kind = "file" if body.kind == "file" else "dir"
        try:
            path = pick_path(kind, body.initial)
        except Exception as e:  # noqa: BLE001, no display or no tkinter
            raise HTTPException(500, f"picker unavailable: {e}")
        return {"path": path}

    # ---- instance ------------------------------------------------------------
    @app.get("/api/instances")
    def instances():
        from .instance.detect import detect_instances
        try:
            found = [i.__dict__ for i in detect_instances()]
        except Exception as e:  # noqa: BLE001
            found = []
            state.log.append(f"instance detection failed: {e}")
        return {"instances": found,
                "recent": state.settings.get("recent_instances", []),
                "current": state.project.instance.model_dump()}

    @app.post("/api/instance/select")
    def instance_select(body: SelectInstanceBody):
        from .instance.detect import describe_folder
        try:
            info = describe_folder(body.path)
        except ValueError as e:
            raise HTTPException(422, str(e))
        proj = state.project
        proj.instance.path = info.game_dir
        proj.instance.launcher = info.launcher
        proj.instance.name = info.name
        proj.instance.loader = info.loader
        proj.instance.mc_version = info.mc_version
        state.set_project(proj)
        recent = [r for r in state.settings.get("recent_instances", []) if r != info.game_dir]
        state.settings["recent_instances"] = [info.game_dir] + recent[:9]
        save_settings(state.settings)

        def do_scan(project: Project, reporter: Reporter) -> None:
            from .instance.scan import scan_instance
            result = scan_instance(info, reporter)
            project.scan_json.write_text(json.dumps(result.to_json(), indent=1), encoding="utf-8")
            state.scan = result
            reporter.log(f"scan: {len(result.mods)} mods, {len(result.biomes)} biomes, "
                         f"{len(result.structures)} structures, {len(result.ores)} ores")
            for w in result.warnings[:20]:
                reporter.log(f"warning: {w}")

        state.start_job([("scan", "Scan instance", do_scan)])
        return {"instance": proj.instance.model_dump(), "job": state.job.status}

    @app.get("/api/scan")
    def scan():
        if state.scan is None:
            raise HTTPException(404, "no scan yet")
        return state.scan.to_json()

    # ---- catalogs ------------------------------------------------------------
    def vanilla_biomes() -> list[str]:
        try:
            from .pipeline.biomes import VANILLA_OVERWORLD_BIOMES
            return list(VANILLA_OVERWORLD_BIOMES)
        except Exception:  # noqa: BLE001
            from .instance.vanilla import OVERWORLD_BIOMES
            return list(OVERWORLD_BIOMES)

    @app.get("/api/biomes/catalog")
    def biomes_catalog():
        vanilla = vanilla_biomes()
        modded: list[str] = []
        if state.scan is not None:
            try:
                modded = sorted(b for b in state.scan.overworld_biomes() if b not in set(vanilla))
            except Exception:  # noqa: BLE001
                modded = []
        table = json.loads((paths.resource_dir() / "data" / "kg_classes.json").read_text(encoding="utf-8"))
        kg = {v["code"]: {"num": int(k), "name": v["name"], "rgb": v["rgb"]} for k, v in table.items()}
        return {"vanilla": vanilla, "modded": modded, "kg": kg}

    @app.get("/api/biomes/defaults")
    def biomes_defaults():
        from .pipeline.defaults import default_biome_settings
        return {"biomes": default_biome_settings().model_dump(mode="json")}

    @app.get("/api/structures/catalog")
    def structures_catalog():
        from .pipeline.defaults import default_structure_entries
        rows: dict[str, dict] = {}
        for e in default_structure_entries():
            rows[e.id] = {"id": e.id, "source": "vanilla", "step": "", "placement": e.placement,
                          "biomes": e.biomes, "count": e.count, "large": e.large, "note": e.note}
        if state.scan is not None:
            tags = getattr(state.scan, "biome_tags", {}) or {}
            for sid, sdef in getattr(state.scan, "structures", {}).items():
                if sid in rows:
                    continue
                rows[sid] = {"id": sid, "source": getattr(sdef, "source", ""),
                             "step": getattr(sdef, "step", ""),
                             "placement": suggested_placement(sdef, tags),
                             "biomes": [], "count": 10, "large": False, "note": ""}
        return {"structures": sorted(rows.values(), key=lambda r: r["id"])}

    @app.get("/api/tools")
    def tools():
        wp = None
        try:
            from .pipeline.worldpainter import find_wpscript
            wp = find_wpscript(state.project.tools.wpscript_path)
        except Exception:  # noqa: BLE001
            wp = None
        caches = {k: dir_size(paths.cache_dir(k)) for k in ("dem", "rivers", "climate")}
        return {"wpscript": {"found": bool(wp), "path": wp or "",
                             "configured": state.project.tools.wpscript_path},
                "java": java_info(), "caches": caches,
                "frozen": bool(getattr(sys, "frozen", False))}

    # ---- jobs ----------------------------------------------------------------
    @app.post("/api/job/start")
    def job_start(body: JobStartBody):
        from .pipeline import runner
        wanted = set(body.steps)
        steps = [(k, label, fn) for k, label, fn in runner.STEPS if k in wanted]
        if not steps:
            raise HTTPException(422, "no known steps requested")
        state.log.clear()
        job = state.start_job(steps, body.overwrite_world)
        return job.status

    @app.post("/api/job/cancel")
    def job_cancel():
        if state.job is None or not state.job.status["running"]:
            return {"cancelled": False}
        state.job.reporter.cancel()
        return {"cancelled": True}

    @app.get("/api/job")
    def job_status():
        try:
            from .pipeline import runner
            outputs = runner.step_status(state.project)
            step_list = [{"key": k, "label": label} for k, label, _ in runner.STEPS]
        except Exception as e:  # noqa: BLE001
            outputs = {}
            step_list = []
            state.log.append(f"runner unavailable: {e}")
        return {"job": state.job.status if state.job else None,
                "log": list(state.log),
                "outputs": outputs,
                "available_steps": step_list}

    @app.get("/api/job/events")
    async def job_events(limit: int = 0):
        # limit > 0 closes the stream after that many frames (used by tests)
        q = state.subscribe()

        async def gen():
            # polled with short sleeps rather than a blocking get so the stream
            # can be cancelled when the browser disconnects
            import asyncio
            sent = 0
            try:
                yield sse({"type": "hello", "running": state.job_running()})
                sent += 1
                last = time.monotonic()
                while not limit or sent < limit:
                    try:
                        ev = q.get_nowait()
                    except queue.Empty:
                        if time.monotonic() - last >= HEARTBEAT_S:
                            last = time.monotonic()
                            yield sse({"type": "heartbeat"})
                        await asyncio.sleep(0.15)
                        continue
                    last = time.monotonic()
                    yield sse(ev)
                    sent += 1
            finally:
                state.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- outputs -------------------------------------------------------------
    @app.get("/api/preview/meta")
    def preview_meta(name: str = "biomes"):
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(400, "bad name")
        f = state.project.preview_dir / f"{name}.json"
        if not f.exists():
            f = state.project.preview_dir / "meta.json"
        if not f.exists():
            raise HTTPException(404, "no preview yet")
        meta = json.loads(f.read_text(encoding="utf-8"))
        b = state.project.bbox
        bb = meta.get("bbox", {})
        meta["current"] = all(abs(float(bb.get(k, 1e9)) - float(getattr(b, k))) < 1e-6
                              for k in ("north", "south", "west", "east"))
        return JSONResponse(meta, headers={"Cache-Control": "no-store"})

    @app.get("/api/preview/{name}.png")
    def preview(name: str):
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(400, "bad name")
        f = state.project.preview_dir / f"{name}.png"
        if not f.exists():
            raise HTTPException(404, "no preview yet")
        return FileResponse(f, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/commands")
    def commands():
        f = state.project.out_dir / "commands.txt"
        if not f.exists():
            raise HTTPException(404, "no commands yet, run the build first")
        return PlainTextResponse(f.read_text(encoding="utf-8"))

    return app

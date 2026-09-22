"""Ordered pipeline steps and the helpers the server and CLI use to run them."""
from __future__ import annotations

from typing import Callable

from ..progress import Reporter
from ..project import Project

StepFn = Callable[[Project, Reporter], None]


def _climate(project: Project, reporter: Reporter) -> None:
    from PIL import Image

    from ..data.climate import fetch_climate
    reporter.set_step("climate")
    grid = project.grid()
    arr = fetch_climate(grid, reporter)
    Image.fromarray(arr, mode="L").save(project.climate_png)
    reporter.log(f"wrote {project.climate_png} ({grid.width}x{grid.height})")
    reporter.progress(1.0)


def _heightmap(project, reporter):
    from .heightmap import run
    run(project, reporter)


def _rivers(project, reporter):
    from .rivers import run
    run(project, reporter)


def _biomes(project, reporter):
    from .biomes import run
    run(project, reporter)


def _export(project, reporter):
    from .worldpainter import run
    run(project, reporter)


def _level(project, reporter):
    from .level_meta import run
    run(project, reporter)


def _remap(project, reporter):
    from .remap import run
    run(project, reporter)


def _void(project, reporter):
    from .void_pack import run
    run(project, reporter)


def _structures(project, reporter):
    from .structures import run
    run(project, reporter)


STEPS: list[tuple[str, str, StepFn]] = [
    ("climate", "Climate raster", _climate),
    ("heightmap", "Heightmap", _heightmap),
    ("rivers", "Rivers", _rivers),
    ("biomes", "Biome map", _biomes),
    ("export", "WorldPainter export", _export),
    ("level", "level.dat spawn and border", _level),
    ("remap", "Modded biome swap", _remap),
    ("void", "Void beyond the map", _void),
    ("structures", "Structure placer", _structures),
]
STEP_KEYS = [k for k, _, _ in STEPS]


def step_fn(key: str) -> StepFn:
    for k, _label, fn in STEPS:
        if k == key:
            return fn
    raise KeyError(f"unknown step {key!r}")


def run_steps(project: Project, keys: list[str], reporter: Reporter) -> None:
    """Run the requested steps in pipeline order, whatever order keys arrived in."""
    unknown = [k for k in keys if k not in STEP_KEYS]
    if unknown:
        raise KeyError(f"unknown steps: {unknown}")
    for key, label, fn in STEPS:
        if key not in keys:
            continue
        reporter.check_cancel()
        reporter.log(f"== {label}")
        fn(project, reporter)


def step_status(project: Project) -> dict[str, bool]:
    """Which step outputs already exist on disk."""
    out = project.out_dir
    world = project.world_dir()
    from .remap import DONE_MARKER
    from .void_pack import PACK_NAME
    return {
        "climate": project.climate_png.exists(),
        "heightmap": project.heightmap_png.exists() and project.sea_mask_png.exists(),
        "rivers": project.rivers_mask_png.exists() or (not project.rivers.enabled and project.heightmap_png.exists()),
        "biomes": project.biomes_png.exists() and project.biomes_palette_json.exists(),
        "export": (world / "level.dat").exists(),
        "level": (world / "level.dat.wpbak").exists(),
        "remap": (out / DONE_MARKER).exists(),
        "void": (world / "datapacks" / PACK_NAME).is_dir() or not project.world.void_outside,
        "structures": (world / "datapacks" / project.structures.namespace).is_dir(),
    }

"""Post-export level.dat surgery: spawn point, backstop world border, generator.

WorldPainter writes a fresh level.dat on every export with no spawn or border
tuning, so this runs after each export. The real rectangular border is a
ChunkyBorder command (see out/commands.txt); the vanilla square border written here
is a backstop for servers without that mod.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from .. import geo
from ..progress import Reporter
from ..project import Project


def noise_generator():
    from nbtlib.tag import Compound, String
    return Compound({
        "type": String("minecraft:noise"),
        "settings": String("minecraft:overworld"),
        "biome_source": Compound({
            "type": String("minecraft:multi_noise"),
            "preset": String("minecraft:overworld"),
        }),
    })


def void_generator():
    from nbtlib.tag import Byte, Compound, List, String
    return Compound({
        "type": String("minecraft:flat"),
        "settings": Compound({
            "biome": String("minecraft:the_void"),
            "lakes": Byte(0),
            "features": Byte(1),
            "layers": List[Compound]([]),
            "structure_overrides": List[String]([]),
        }),
    })


def apply(level_dat: Path, spawn: tuple[int, int, int], border_size: float | None,
          generator: str = "noise") -> None:
    import nbtlib
    from nbtlib.tag import Double, Int

    f = nbtlib.load(str(level_dat))
    d = f["Data"]
    if generator in ("noise", "void"):
        try:
            ow = d["WorldGenSettings"]["dimensions"]["minecraft:overworld"]
            ow["generator"] = noise_generator() if generator == "noise" else void_generator()
        except KeyError:
            pass  # a level.dat without dimension settings keeps whatever it has
    sx, sy, sz = spawn
    d["SpawnX"], d["SpawnY"], d["SpawnZ"] = Int(sx), Int(sy), Int(sz)
    if border_size is not None:
        d["BorderCenterX"], d["BorderCenterZ"] = Double(0.0), Double(0.0)
        d["BorderSize"] = Double(float(border_size))
    f.save(str(level_dat))


def spawn_for(project: Project) -> tuple[int, int, int]:
    """World spawn from the project's lat/lon (else the map centre), Y from the baked
    surface when the heightmap is available."""
    g = project.grid()
    w = project.world
    if w.spawn_lat is not None and w.spawn_lon is not None:
        col, row = g.lonlat_to_px(w.spawn_lon, w.spawn_lat)
        col = min(max(int(col), 0), g.width - 1)
        row = min(max(int(row), 0), g.height - 1)
    else:
        col, row = g.width // 2, g.height // 2
    x, z = g.px_to_world(col, row)
    y = w.spawn_y
    if project.heightmap_png.exists():
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(project.heightmap_png) as im:
            v = im.getpixel((col, row))
        if isinstance(v, tuple):
            v = v[0]
        y = int(geo.u16_to_y(np.uint16(v), project.scale.build_low, project.scale.build_high)) + 2
        y = max(y, project.scale.water_level + 1)
    return x, y, z


def run(project: Project, reporter: Reporter) -> None:
    reporter.set_step("level")
    level = project.world_dir() / "level.dat"
    if not level.exists():
        raise RuntimeError(f"{level} not found; export the world first")
    bak = level.with_name("level.dat.wpbak")
    if not bak.exists():
        shutil.copy2(level, bak)
        reporter.log(f"kept as-exported copy {bak.name}")
    g = project.grid()
    border = 2 * max(g.border_radius_x, g.border_radius_z) + 64 if project.world.vanilla_border else None
    spawn = spawn_for(project)
    apply(level, spawn, border, "noise")
    reporter.log(f"spawn {spawn[0]},{spawn[1]},{spawn[2]}" + (f"  backstop border {border:.0f}" if border else ""))
    reporter.progress(1.0)

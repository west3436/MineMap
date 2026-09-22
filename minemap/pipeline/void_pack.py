"""The minemap_void datapack: nothing generates beyond the baked map.

Fresh overworld chunks get an all-air noise setting (final density -1), and every
overworld structure set is overridden to an empty list so nothing floats in the
void. Chunks WorldPainter already wrote are saved as full and never regenerate.
Nether and End sets stay untouched. A datapack is used instead of a level.dat
generator swap because TerraBlender style mods force the overworld generator at
load and ignore level.dat.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..progress import Reporter
from ..project import Project

PACK_NAME = "minemap_void"

# Minecraft version -> datapack pack_format. Approximate for the newest entries.
PACK_FORMATS: list[tuple[str, int]] = [
    ("1.20.5", 41), ("1.20.6", 41),
    ("1.21", 48), ("1.21.1", 48),
    ("1.21.2", 57), ("1.21.3", 57),
    ("1.21.4", 61),
    ("1.21.5", 71),
    ("1.21.6", 81), ("1.21.7", 81), ("1.21.8", 81),
]
DEFAULT_PACK_FORMAT = 48


def pack_format_for(mc_version: str) -> int:
    v = (mc_version or "").strip()
    for ver, fmt in PACK_FORMATS:
        if v == ver:
            return fmt
    # unknown newer version: use the highest known format
    if v:
        try:
            parts = tuple(int(p) for p in v.split("."))
            best = DEFAULT_PACK_FORMAT
            for ver, fmt in PACK_FORMATS:
                if tuple(int(p) for p in ver.split(".")) <= parts:
                    best = fmt
            return best
        except ValueError:
            pass
    return DEFAULT_PACK_FORMAT


VOID_NOISE_SETTINGS = {
    "sea_level": -64,
    "disable_mob_generation": True,
    "aquifers_enabled": False,
    "ore_veins_enabled": False,
    "legacy_random_source": False,
    "default_block": {"Name": "minecraft:air"},
    "default_fluid": {"Name": "minecraft:air"},
    "spawn_target": [],
    "noise": {"min_y": -64, "height": 384, "size_horizontal": 1, "size_vertical": 2},
    "noise_router": {
        "barrier": 0, "fluid_level_floodedness": 0, "fluid_level_spread": 0, "lava": 0,
        "temperature": 0, "vegetation": 0, "continents": 0, "erosion": 0, "depth": 0,
        "ridges": 0, "initial_density_without_jaggedness": 0, "final_density": -1,
        "vein_toggle": 0, "vein_ridged": 0, "vein_gap": 0,
    },
    "surface_rule": {"type": "minecraft:block", "result_state": {"Name": "minecraft:air"}},
}

EMPTY_SET = {
    "structures": [],
    "placement": {"type": "minecraft:random_spread", "spacing": 32, "separation": 8, "salt": 0},
}


def overworld_structure_sets(scan: dict | None) -> list[str]:
    """Structure set ids to disable, from the scan JSON when present, else vanilla."""
    if scan and scan.get("structure_sets"):
        out = []
        for sid, sdef in scan["structure_sets"].items():
            kind = (sdef.get("kind") or "overworld") if isinstance(sdef, dict) else "overworld"
            if kind in ("overworld", "unknown", ""):
                out.append(sid)
        if out:
            return sorted(out)
    from ..instance import vanilla
    non = set(getattr(vanilla, "NON_OVERWORLD_SETS", ()))
    return sorted(s for s in vanilla.STRUCTURE_SETS if s not in non)


def write_pack(dest: Path, pack_format: int, set_ids: list[str]) -> int:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "pack.mcmeta").write_text(json.dumps({"pack": {
        "pack_format": pack_format,
        "description": "MineMap: void beyond the baked map and no natural overworld structures. "
                       "Remove this pack to restore normal terrain outside the map.",
    }}, indent=2), encoding="utf-8")
    ns_dir = dest / "data" / "minecraft" / "worldgen" / "noise_settings"
    ns_dir.mkdir(parents=True)
    (ns_dir / "overworld.json").write_text(json.dumps(VOID_NOISE_SETTINGS, indent=2), encoding="utf-8")
    n = 0
    for sid in set_ids:
        ns, _, name = sid.partition(":")
        if not name:
            ns, name = "minecraft", ns
        d = dest / "data" / ns / "worldgen" / "structure_set"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps(EMPTY_SET, indent=2), encoding="utf-8")
        n += 1
    return n


def run(project: Project, reporter: Reporter) -> None:
    reporter.set_step("void")
    dest = project.world_dir() / "datapacks" / PACK_NAME
    if not project.world.void_outside:
        if dest.exists():
            shutil.rmtree(dest)
        reporter.log("void outside the map is off; pack not written")
        reporter.progress(1.0)
        return
    if not project.world_dir().exists():
        raise RuntimeError(f"{project.world_dir()} not found; export the world first")
    scan = None
    if project.scan_json.exists():
        try:
            scan = json.loads(project.scan_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reporter.log("instance scan unreadable; using the vanilla structure set list")
    fmt = pack_format_for(project.instance.mc_version)
    sets = overworld_structure_sets(scan)
    n = write_pack(dest, fmt, sets)
    reporter.log(f"wrote {dest} (pack_format {fmt}, {n} structure sets disabled)")
    reporter.progress(1.0)

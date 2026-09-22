"""Structure placer: a datapack of /place functions for vanilla and modded structures.

WorldPainter bakes chunks past the structure step, so nothing generates on its own.
Instead we choose sites on the biome and height rasters and emit a datapack whose
functions forceload a few chunks at a time, /place each structure, then move on.

Y handling (the float fix): /place derives Y from the structure's start_height and
projection. A WORLD_SURFACE or OCEAN_FLOOR projection resolves against the chunk
generator's noise surface, which has nothing to do with the baked terrain, so those
structures float. For them we emit an override structure with the projection dropped
and start_height pinned to the baked surface at that site (plus the structure's own
absolute offset). Structures without a projection place at their designed Y already.
"""
from __future__ import annotations

import copy
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .. import geo
from ..progress import Reporter
from ..project import Project, StructureEntry
from .vanilla_structures import DEFAULT_BY_ID, REFERENCE_AREA, SELF_POSITIONING

Image.MAX_IMAGE_PIXELS = None

PLACE_DELAY_T = 40     # ticks between forceloading a batch and placing it
BATCH_PERIOD_T = 10    # ticks between batches inside a wave
WAVE_GAP_T = 100       # ticks of breathing room between auto-advanced waves


# ---------------------------------------------------------------- Y classification
def start_offset(start_height) -> int | None:
    if not isinstance(start_height, dict):
        return 0
    if "absolute" in start_height:
        return int(start_height["absolute"])
    t = str(start_height.get("type", ""))
    if t.endswith("uniform"):
        try:
            lo = start_height["min_inclusive"]["absolute"]
            hi = start_height["max_inclusive"]["absolute"]
            return int(round((lo + hi) / 2))
        except (KeyError, TypeError):
            return None
    return None


def classify_ymode(sdef) -> tuple[str, int]:
    """('plain' | 'surface' | 'ocean_floor', offset). sdef has .start_height and .projection."""
    if sdef is None:
        return ("plain", 0)
    proj = getattr(sdef, "projection", None)
    off = start_offset(getattr(sdef, "start_height", None))
    if proj == "OCEAN_FLOOR":
        return ("ocean_floor", off or 0)
    if proj in ("WORLD_SURFACE", "WORLD_SURFACE_WG"):
        return ("surface", off or 0)
    return ("plain", 0)


def make_override(raw: dict, abs_y: int) -> dict:
    o = copy.deepcopy(raw)
    o.pop("project_start_to_heightmap", None)
    o["start_height"] = {"absolute": int(abs_y)}
    return o


# ---------------------------------------------------------------- rasters
@dataclass
class Rasters:
    B_ds: np.ndarray          # uint8 biome index, down-sampled
    Hm_ds: np.ndarray         # uint16 heightmap, down-sampled
    final_names: dict[int, str]
    stride: int
    height: int
    width: int


def load_rasters(project: Project, stride: int) -> Rasters:
    pal = json.loads(project.biomes_palette_json.read_text(encoding="utf-8"))
    remap = {}
    if project.biomes_remap_json.exists():
        remap = json.loads(project.biomes_remap_json.read_text(encoding="utf-8"))
    final = {int(k): remap.get(v, v) for k, v in pal.items()}
    B = np.asarray(Image.open(project.biomes_png))
    if B.ndim == 3:
        B = B[:, :, 0]
    H, W = B.shape
    hm = np.asarray(Image.open(project.heightmap_png))
    if hm.ndim == 3:
        hm = hm[:, :, 0]
    return Rasters(B[::stride, ::stride].copy(), hm[::stride, ::stride].astype(np.uint16).copy(),
                   final, stride, H, W)


def local_relief(a: np.ndarray, k: int) -> np.ndarray:
    """Window max minus min; lower is flatter."""
    a = a.astype(np.int32)
    H, W = a.shape
    pad = np.pad(a, k, mode="edge")
    mx = np.full((H, W), np.iinfo(np.int32).min, np.int32)
    mn = np.full((H, W), np.iinfo(np.int32).max, np.int32)
    for dy in range(2 * k + 1):
        for dx in range(2 * k + 1):
            sub = pad[dy:dy + H, dx:dx + W]
            np.maximum(mx, sub, out=mx)
            np.minimum(mn, sub, out=mn)
    return mx - mn


def make_water_masks(B_ds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    water = B_ds == 0
    adj = np.zeros_like(water)
    adj[:-1, :] |= water[1:, :]
    adj[1:, :] |= water[:-1, :]
    adj[:, :-1] |= water[:, 1:]
    adj[:, 1:] |= water[:, :-1]
    return water, (~water) & adj


def deep_water_mask(water: np.ndarray) -> np.ndarray:
    inner = water.copy()
    inner[:-1, :] &= water[1:, :]
    inner[1:, :] &= water[:-1, :]
    inner[:, :-1] &= water[:, 1:]
    inner[:, 1:] &= water[:, :-1]
    return inner


def deconflict(items, sep: int):
    """Nudge (id, x, z, extra) placements apart so none are within sep blocks."""
    offs = [(0, 0)]
    for r in range(1, 9):
        d = r * sep
        offs += [(d, 0), (-d, 0), (0, d), (0, -d), (d, d), (-d, d), (d, -d), (-d, -d)]
    accepted, result = [], []
    for sid, x, z, extra in items:
        nx, nz = x, z
        for dx, dz in offs:
            nx, nz = x + dx, z + dz
            if all(abs(nx - ax) >= sep or abs(nz - az) >= sep for ax, az in accepted):
                break
        accepted.append((nx, nz))
        result.append((sid, nx, nz, extra))
    return result


# ---------------------------------------------------------------- biome resolution
def _is_ocean(b: str) -> bool:
    return b.endswith("ocean")


def resolve_biome_ids(raw, biome_tags: dict[str, list[str]]) -> set[str]:
    """A structure def's biomes field (tag string, id, or list) -> set of biome ids."""
    out: set[str] = set()
    if raw is None:
        return out
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    for it in items:
        if not isinstance(it, str):
            continue
        if it.startswith("#"):
            out.update(x for x in biome_tags.get(it, biome_tags.get(it[1:], [])) if not x.startswith("#"))
        else:
            out.add(it)
    return out


def mask_for_biomes(ids: set[str], final_names: dict[int, str], B_ds, water, deep, coast):
    if not ids:
        return None
    if all(_is_ocean(b) for b in ids):
        return deep if (deep.any() and all(b.startswith("minecraft:deep_") for b in ids)) else water
    if any("beach" in b or "shore" in b for b in ids):
        return coast
    idxs = [i for i, n in final_names.items() if n in ids]
    if not idxs:
        # The structure has a real biome list and none of it is painted here, so it
        # simply does not belong on this map. An empty mask places nothing.
        return np.zeros(B_ds.shape, dtype=bool)
    return np.isin(B_ds, idxs)


def resolve_mask(entry: StructureEntry, sdef, scan, final_names: dict[int, str],
                 B_ds, water, deep, coast) -> np.ndarray:
    land = B_ds > 0
    if entry.placement == "ocean":
        return water
    if entry.placement == "ocean_deep":
        return deep if deep.any() else water
    if entry.placement == "coast":
        return coast
    if entry.placement == "any_land":
        return land
    if entry.placement == "land":
        return land
    # auto: explicit biome list first, then the def's tags
    ids = set(entry.biomes)
    if not ids and sdef is not None:
        tags = getattr(scan, "biome_tags", {}) if scan is not None else {}
        ids = resolve_biome_ids(getattr(sdef, "biomes", None), tags)
    m = mask_for_biomes(ids, final_names, B_ds, water, deep, coast)
    return land if m is None else m


# ---------------------------------------------------------------- placement
def pick_cells(mask, rough, count, large, rng, flat_p=45, flat_p_large=20):
    m = mask
    if m.any():
        thr = np.percentile(rough[m], flat_p_large if large else flat_p)
        m = m & (rough <= thr)
    flat = np.flatnonzero(m)
    if flat.size == 0:
        return []
    n = min(count, flat.size)
    chosen = rng.choice(flat, size=n, replace=False)
    ii, jj = np.divmod(chosen, mask.shape[1])
    return list(zip(ii.tolist(), jj.tolist()))


def effective_count(entry: StructureEntry, density: float, area: int) -> int:
    if not entry.enabled:
        return 0
    return max(1, int(round(entry.count * density * area / REFERENCE_AREA)))


def build_placements(entries: list[StructureEntry], scan, rasters: Rasters, grid: geo.Grid,
                     density: float, seed: int, namespace: str,
                     build_low: int = -64, build_high: int = 320):
    """Returns (placements, overrides, log). placements = [(place_id, x, z, large)]."""
    B_ds, Hm_ds = rasters.B_ds, rasters.Hm_ds
    water, coast = make_water_masks(B_ds)
    deep = deep_water_mask(water)
    rough = local_relief(Hm_ds, 3)
    rng = np.random.default_rng(seed)
    D = rasters.stride
    area = rasters.width * rasters.height
    sdefs = getattr(scan, "structures", {}) if scan is not None else {}
    placements, overrides, log = [], {}, []
    for e in entries:
        if not e.enabled:
            continue
        sdef = sdefs.get(e.id)
        if sdef is None and e.id not in DEFAULT_BY_ID:
            log.append((e.id, "skipped", 0, 0, "not installed"))
            continue
        ymode, offset = ("plain", 0) if e.id in SELF_POSITIONING else classify_ymode(sdef)
        raw = getattr(sdef, "raw", None) if sdef is not None else None
        if ymode != "plain" and not isinstance(raw, dict):
            ymode = "plain"
        mask = resolve_mask(e, sdef, scan, rasters.final_names, B_ds, water, deep, coast)
        want = effective_count(e, density, area)
        cells = pick_cells(mask, rough, want, e.large, rng)
        for i_ds, j_ds in cells:
            x = j_ds * D + D // 2 + grid.shift_x
            z = i_ds * D + D // 2 + grid.shift_z
            if ymode == "plain":
                pid = e.id
            else:
                y = int(geo.u16_to_y(np.uint16(Hm_ds[i_ds, j_ds]), build_low, build_high)) + offset
                oid = f"{e.id.split(':')[-1]}_y{y}".replace("-", "n")
                if oid not in overrides:
                    overrides[oid] = make_override(raw, y)
                pid = f"{namespace}:{oid}"
            placements.append((pid, x, z, e.large))
        log.append((e.id, ymode, want, len(cells), e.placement))
    return placements, overrides, log


# ---------------------------------------------------------------- datapack writer
def _tellraw(text, color="green"):
    return 'tellraw @a {"text":"%s","color":"%s"}' % (text, color)


def place_cmd(place_id, x, z):
    return f"execute positioned {x} 0 {z} run place structure {place_id}"


def write_datapack(dp_dir: Path, ns: str, placements, overrides, batch: int, waves: int,
                   pack_format: int = 48) -> int:
    if dp_dir.is_dir():
        shutil.rmtree(dp_dir)
    fn = dp_dir / "data" / ns / "function"
    fn.mkdir(parents=True)
    sdir = dp_dir / "data" / ns / "worldgen" / "structure"
    sdir.mkdir(parents=True)
    for oid, odef in overrides.items():
        (sdir / f"{oid}.json").write_text(json.dumps(odef), encoding="utf-8")

    def box(x, z, large):
        r = 96 if large else 32
        return x - r, z - r, x + r, z + r

    n = len(placements)
    nbatch = max(1, math.ceil(n / batch))
    W = max(1, min(waves, nbatch))
    base, rem = divmod(nbatch, W)
    wave_first, idx = [], 0
    for k in range(W):
        wave_first.append(idx)
        idx += base + (1 if k < rem else 0)
    wave_last = [wave_first[k + 1] - 1 for k in range(W - 1)] + [nbatch - 1]
    boundary = set(wave_last)
    wave_of = {b: k for k in range(W) for b in range(wave_first[k], wave_last[k] + 1)}
    cum = [min((wave_last[k] + 1) * batch, n) for k in range(W)]
    cnt = [cum[k] - (cum[k - 1] if k else 0) for k in range(W)]
    secs = [max(1, round((wave_last[k] - wave_first[k] + 1) * BATCH_PERIOD_T / 20)) for k in range(W)]

    (dp_dir / "pack.mcmeta").write_text(json.dumps({"pack": {
        "pack_format": pack_format,
        "description": "MineMap structure placer (%d structures, %d overrides, %d waves)" % (n, len(overrides), W),
    }}, indent=2), encoding="utf-8")

    def w(name, lines):
        (fn / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    for b in range(nbatch):
        items = placements[b * batch:(b + 1) * batch]
        bl = [f"forceload add {x0} {z0} {x1} {z1}" for _p, x, z, large in items
              for x0, z0, x1, z1 in [box(x, z, large)]]
        bl.append(f"schedule function {ns}:p{b:05d} {PLACE_DELAY_T}t")
        if b not in boundary:
            bl.append(f"schedule function {ns}:b{b + 1:05d} {BATCH_PERIOD_T}t")
        w(f"b{b:05d}.mcfunction", bl)

        pl = [place_cmd(pid, x, z) for pid, x, z, _ in items]
        for _pid, x, z, large in items:
            x0, z0, x1, z1 = box(x, z, large)
            pl.append(f"forceload remove {x0} {z0} {x1} {z1}")
        if b in boundary:
            k = wave_of[b]
            pl.append("forceload remove all")
            pl.append(f"scoreboard players set #busy {ns} 0")
            if k < W - 1:
                pl.append(f"execute if score #auto {ns} matches 1 run schedule function {ns}:next {WAVE_GAP_T}t")
                pl.append(f"execute if score #auto {ns} matches 1 run " + _tellraw(
                    "[MineMap] Wave %d/%d done (%d/%d placed); next wave in %ds."
                    % (k + 1, W, cum[k], n, max(1, WAVE_GAP_T // 20))))
                pl.append(f"execute if score #auto {ns} matches 0 run " + _tellraw(
                    "[MineMap] Wave %d/%d done (%d/%d placed). Run /function %s:next for wave %d."
                    % (k + 1, W, cum[k], n, ns, k + 2)))
            else:
                pl.append(f"scoreboard players set #done {ns} 1")
                pl.append(f"scoreboard players set #auto {ns} 0")
                pl.append(_tellraw("[MineMap] All %d waves complete, %d structures placed." % (W, n)))
        w(f"p{b:05d}.mcfunction", pl)

    for k in range(W):
        w(f"wave{k + 1}.mcfunction", [
            f"scoreboard objectives add {ns} dummy",
            f'execute if score #busy {ns} matches 1 run tellraw @s '
            '{"text":"[MineMap] A wave is still placing; wait for the green complete message.","color":"red"}',
            f"execute if score #busy {ns} matches 1 run return 0",
            f"scoreboard players set #busy {ns} 1",
            f"scoreboard players set #wave {ns} %d" % (k + 1),
            _tellraw("[MineMap] Placing wave %d/%d (%d structures, about %ds). Stay in the world."
                     % (k + 1, W, cnt[k], secs[k]), "yellow"),
            f"function {ns}:b%05d" % wave_first[k],
        ])

    nxt = [
        f"scoreboard objectives add {ns} dummy",
        f'execute if score #busy {ns} matches 1 run tellraw @s '
        '{"text":"[MineMap] A wave is still placing; wait for it to finish.","color":"red"}',
        f"execute if score #busy {ns} matches 1 run return 0",
        f'execute if score #wave {ns} matches %d.. run tellraw @s '
        '{"text":"[MineMap] All %d waves already placed. /function %s:reset to redo.","color":"red"}' % (W, W, ns),
        f"execute if score #wave {ns} matches %d.. run return 0" % W,
        f"scoreboard players add #wave {ns} 1",
    ]
    for k in range(1, W + 1):
        nxt.append(f"execute if score #wave {ns} matches %d run function {ns}:wave%d" % (k, k))
    w("next.mcfunction", nxt)

    def reset_lines(auto: int):
        return [
            f"scoreboard objectives add {ns} dummy",
            f"scoreboard players set #wave {ns} 0",
            f"scoreboard players set #busy {ns} 0",
            f"scoreboard players set #done {ns} 0",
            f"scoreboard players set #auto {ns} {auto}",
            "forceload remove all",
        ]

    w("run.mcfunction", reset_lines(0) + [
        _tellraw("[MineMap] %d structures in %d waves. Starting wave 1; run /function %s:next for each "
                 "following wave, or /function %s:auto to advance automatically." % (n, W, ns, ns), "yellow"),
        f"function {ns}:wave1",
    ])
    w("auto.mcfunction", reset_lines(1) + [
        _tellraw("[MineMap] %d structures in %d waves, advancing automatically. Stay in the world until "
                 "the green complete message." % (n, W), "yellow"),
        f"function {ns}:wave1",
    ])
    w("reset.mcfunction", reset_lines(0) + [
        _tellraw("[MineMap] Placer reset. /function %s:run, :auto, or :next to place again." % ns, "yellow"),
    ])

    # a near-origin eyeball test: one large structure and two small ones nearest spawn
    def short_of(pid):
        return pid.split(":")[-1].split("_y")[0]

    picks = []
    seen = set()
    for p in sorted(placements, key=lambda p: abs(p[1]) + abs(p[2])):
        s = short_of(p[0])
        if s in seen:
            continue
        seen.add(s)
        picks.append(p)
        if len(picks) >= 3:
            break
    test = [f"scoreboard objectives add {ns} dummy"]
    for _pid, x, z, large in picks:
        x0, z0, x1, z1 = box(x, z, large)
        test.append(f"forceload add {x0} {z0} {x1} {z1}")
    test.append(f"schedule function {ns}:testplace {PLACE_DELAY_T}t")
    test.append(_tellraw("[MineMap] Test: placing %d sample structures near spawn." % len(picks), "yellow"))
    w("test.mcfunction", test)
    tp = [place_cmd(pid, x, z) for pid, x, z, _ in picks]
    for _pid, x, z, large in picks:
        x0, z0, x1, z1 = box(x, z, large)
        tp.append(f"forceload remove {x0} {z0} {x1} {z1}")
    names = ", ".join(short_of(p[0]) for p in picks) or "none"
    tp.append(_tellraw("[MineMap] Test structures placed (%s). If they sit right, run /function %s:run." % (names, ns)))
    w("testplace.mcfunction", tp)

    (dp_dir / "README.txt").write_text(
        "MineMap structure placer datapack\n\n"
        f"Structures: {n}   override defs: {len(overrides)}   waves: {W}\n\n"
        "In game, after the world has loaded once:\n"
        f"  /function {ns}:test     place three samples near spawn to check the height\n"
        f"  /function {ns}:run      start wave 1, then /function {ns}:next per wave\n"
        f"  /function {ns}:auto     run every wave hands free\n"
        f"  /function {ns}:reset    clear the wave counters\n",
        encoding="utf-8")
    return W


def write_commands(project: Project, ns: str, n_struct: int, waves: int) -> Path:
    g = project.grid()
    lines = [
        f"MineMap commands for world '{project.world.name}'",
        f"map {g.width} x {g.height} blocks, X {g.shift_x}..{g.shift_x + g.width - 1}, Z {g.shift_z}..{g.shift_z + g.height - 1}",
        "",
        "Rectangular world border (ChunkyBorder mod):",
        "  /chunky shape square",
        "  /chunky center 0 0",
        f"  /chunky radius {g.border_radius_x} {g.border_radius_z}",
        "  /chunky border add",
        "",
        "Pre-generate the whole map (Chunky mod, resumable):",
        "  /chunky start",
        "",
    ]
    if n_struct:
        lines += [
            f"Structures ({n_struct} in {waves} waves):",
            f"  /function {ns}:test",
            f"  /function {ns}:run      then /function {ns}:next per wave",
            f"  /function {ns}:auto     hands free",
            "",
        ]
    p = project.out_dir / "commands.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _load_scan(project: Project):
    if not project.scan_json.exists():
        return None
    try:
        from ..instance.scan import ScanResult
        return ScanResult.from_json(json.loads(project.scan_json.read_text(encoding="utf-8")))
    except Exception:
        return None


def run(project: Project, reporter: Reporter) -> None:
    reporter.set_step("structures")
    ss = project.structures
    ns = ss.namespace
    if not ss.enabled:
        write_commands(project, ns, 0, 0)
        reporter.log("structure placement is off; wrote commands.txt only")
        reporter.progress(1.0)
        return
    world = project.world_dir()
    if not world.exists():
        raise RuntimeError(f"{world} not found; export the world first")
    scan = _load_scan(project)
    rasters = load_rasters(project, ss.stride)
    grid = project.grid()
    placements, overrides, log = build_placements(ss.entries, scan, rasters, grid, ss.density, ss.seed, ns,
                                                  project.scale.build_low, project.scale.build_high)
    reporter.progress(0.5)
    xmin, xmax = grid.shift_x, grid.shift_x + grid.width - 1
    zmin, zmax = grid.shift_z, grid.shift_z + grid.height - 1
    clamp = lambda v, lo, hi: max(lo, min(hi, v))
    larges = [(p[0], p[1], p[2], True) for p in placements if p[3]]
    smalls = [p for p in placements if not p[3]]
    spread = deconflict(larges, sep=80)
    placements = [(pid, clamp(x, xmin, xmax), clamp(z, zmin, zmax), True) for pid, x, z, _ in spread] + smalls

    from .void_pack import pack_format_for
    dp = world / "datapacks" / ns
    waves = write_datapack(dp, ns, placements, overrides, ss.batch, ss.waves,
                           pack_format_for(project.instance.mc_version))
    write_commands(project, ns, len(placements), waves)
    for sid, ymode, req, got, cls in sorted(log, key=lambda r: -r[3]):
        reporter.log(f"  {sid:40s} {ymode:11s} {cls:10s} placed={got:<4d} (wanted {req})")
    reporter.log(f"{len(placements)} placements, {len(overrides)} override defs, {waves} waves -> {dp}")
    reporter.progress(1.0)

"""Biome map: climate class + elevation + noise -> painted biome index per pixel.

Outputs biomes.png (uint8 palette index, 0 = leave to WorldPainter), the palette
WorldPainter paints, and the placeholder remap for modded biomes. WorldPainter can
only paint vanilla biome names, so each modded biome borrows an unused vanilla name
here and remap.py swaps the real name into the region files after export.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .. import geo
from ..data.climate import KG_CODES
from ..progress import Reporter
from ..project import BiomeSettings, Project

Image.MAX_IMAGE_PIXELS = None

VANILLA_OVERWORLD_BIOMES: list[str] = [f"minecraft:{b}" for b in (
    "badlands", "bamboo_jungle", "beach", "birch_forest", "cherry_grove", "cold_ocean",
    "dark_forest", "deep_cold_ocean", "deep_frozen_ocean", "deep_lukewarm_ocean", "deep_ocean",
    "desert", "eroded_badlands", "flower_forest", "forest", "frozen_ocean", "frozen_peaks",
    "frozen_river", "grove", "ice_spikes", "jagged_peaks", "jungle", "lukewarm_ocean",
    "mangrove_swamp", "meadow", "mushroom_fields", "ocean", "old_growth_birch_forest",
    "old_growth_pine_taiga", "old_growth_spruce_taiga", "plains", "river", "savanna",
    "savanna_plateau", "snowy_beach", "snowy_plains", "snowy_slopes", "snowy_taiga",
    "sparse_jungle", "stony_peaks", "stony_shore", "sunflower_plains", "swamp", "taiga",
    "warm_ocean", "windswept_forest", "windswept_gravelly_hills", "windswept_hills",
    "windswept_savanna", "wooded_badlands", "dripstone_caves", "lush_caves", "deep_dark",
    "the_void",
)]

# WorldPainter assigns these on its own for unpainted pixels (water, shores, default
# land) or they are cave biomes that must never appear on the surface; a placeholder
# drawn from this set could collide with real automatic paint.
AUTO_BIOMES: set[str] = {f"minecraft:{b}" for b in (
    "ocean", "deep_ocean", "warm_ocean", "lukewarm_ocean", "deep_lukewarm_ocean", "cold_ocean",
    "deep_cold_ocean", "frozen_ocean", "deep_frozen_ocean", "river", "frozen_river", "beach",
    "snowy_beach", "stony_shore", "plains", "the_void", "dripstone_caves", "lush_caves", "deep_dark",
)}


# Second-tier placeholders, used only when every unused surface biome is taken. They
# are swapped away in the region files, so a surface column never keeps them. Cave,
# nether and end biomes are all valid overworld palette entries for WorldPainter's
# 1.20.5 platform.
EXTRA_PLACEHOLDERS: list[str] = [f"minecraft:{b}" for b in (
    "dripstone_caves", "lush_caves", "deep_dark",
    "nether_wastes", "soul_sand_valley", "crimson_forest", "warped_forest", "basalt_deltas",
    "the_end", "end_highlands", "end_midlands", "small_end_islands", "end_barrens",
)]


# ---------------------------------------------------------------- palette
def used_biomes(settings: BiomeSettings) -> list[str]:
    """Every biome id the settings can produce, in a deterministic order."""
    seen: list[str] = []

    def add(b):
        if b and b not in seen:
            seen.append(b)

    for code in sorted(settings.rules):
        for wb in settings.rules[code]:
            add(wb.biome)
    for band in settings.elevation_bands:
        add(band.biome)
    add(settings.river_biome)
    add(settings.fill_unknown)
    return seen


def build_palette(settings: BiomeSettings) -> tuple[list[str | None], dict[str, str]]:
    """Return (palette, remap). palette[idx] is the vanilla name WorldPainter paints
    for index idx (None at 0). remap maps placeholder vanilla name -> modded id."""
    biomes = used_biomes(settings)
    vanilla = set(VANILLA_OVERWORLD_BIOMES)
    taken = {b for b in biomes if b in vanilla}
    pool = [b for b in VANILLA_OVERWORLD_BIOMES if b not in AUTO_BIOMES and b not in taken]
    pool += [b for b in EXTRA_PLACEHOLDERS if b not in taken and b not in pool]
    palette: list[str | None] = [None]
    remap: dict[str, str] = {}
    modded = [b for b in biomes if b not in vanilla]
    if len(modded) > len(pool):
        raise ValueError(
            f"{len(modded)} modded biomes need placeholders but only {len(pool)} unused vanilla "
            f"biomes remain; drop {len(modded) - len(pool)} biome(s) from the mapping")
    for b in biomes:
        if b in vanilla:
            palette.append(b)
        else:
            ph = pool.pop(0)
            palette.append(ph)
            remap[ph] = b
    if len(palette) > 255:
        raise ValueError("more than 254 distinct biomes; the index map is 8-bit")
    return palette, remap


def index_map(settings: BiomeSettings, palette: list[str | None], remap: dict[str, str]) -> dict[str, int]:
    """final biome id -> palette index."""
    out: dict[str, int] = {}
    for idx, name in enumerate(palette):
        if name is None:
            continue
        out[remap.get(name, name)] = idx
    return out


# ---------------------------------------------------------------- noise
def coarse_noise(H: int, W: int, cell: int, seed: int):
    hc, wc = H // cell + 2, W // cell + 2
    return np.random.default_rng(seed).random((hc, wc)).astype(np.float32)


def noise_rows(C, cell: int, r0: int, r1: int, W: int):
    """Bilinear upsample of the coarse grid for rows [r0, r1)."""
    rows = np.arange(r0, r1)
    gy = rows / cell
    gy0 = np.floor(gy).astype(np.int64)
    fy = (gy - gy0).astype(np.float32)
    cols = np.arange(W)
    gx = cols / cell
    gx0 = np.floor(gx).astype(np.int64)
    fx = (gx - gx0).astype(np.float32)
    c00 = C[gy0[:, None], gx0[None, :]]
    c01 = C[gy0[:, None], gx0[None, :] + 1]
    c10 = C[gy0[:, None] + 1, gx0[None, :]]
    c11 = C[gy0[:, None] + 1, gx0[None, :] + 1]
    top = c00 * (1 - fx)[None, :] + c01 * fx[None, :]
    bot = c10 * (1 - fx)[None, :] + c11 * fx[None, :]
    return top * (1 - fy)[:, None] + bot * fy[:, None]


# ---------------------------------------------------------------- classify
def _kg_codes():
    from ..data.climate import KG_CODES
    return KG_CODES


def _band_matches(band, code: str) -> bool:
    if not band.climates:
        return True
    return code in band.climates or (code[:1] in band.climates)


def classify(climate: np.ndarray, elev_m: np.ndarray, noise: np.ndarray, land: np.ndarray,
             settings: BiomeSettings, index_of: dict[str, int]) -> np.ndarray:
    """Vectorised biome assignment for one row band. climate is the KG value (uint8),
    elev_m metres, noise in [0, 1), land a bool mask. Returns uint8 indices."""
    codes = _kg_codes()
    out = np.zeros(climate.shape, dtype=np.uint8)
    fill_idx = index_of.get(settings.fill_unknown, 0)
    bands = sorted(settings.elevation_bands, key=lambda b: -b.min_m)
    present = np.unique(climate[land]) if land.any() else np.array([], dtype=climate.dtype)
    for val in present.tolist():
        code = codes.get(int(val), "")
        sel = land & (climate == val)
        if not sel.any():
            continue
        assigned = np.zeros(climate.shape, dtype=bool)
        for band in bands:
            if not _band_matches(band, code):
                continue
            idx = index_of.get(band.biome)
            if idx is None:
                continue
            m = sel & ~assigned & (elev_m >= band.min_m)
            out[m] = idx
            assigned |= m
        rest = sel & ~assigned
        if not rest.any():
            continue
        rule = settings.rules.get(code)
        if not rule:
            out[rest] = fill_idx
            continue
        weights = np.array([max(0.0, wb.weight) for wb in rule], dtype=np.float32)
        if weights.sum() <= 0:
            weights = np.ones_like(weights)
        cum = np.cumsum(weights / weights.sum())
        pick = np.searchsorted(cum, noise[rest], side="right")
        pick = np.clip(pick, 0, len(rule) - 1)
        idxs = np.array([index_of.get(wb.biome, fill_idx) for wb in rule], dtype=np.uint8)
        out[rest] = idxs[pick]
    return out


def apply_river_overlay(out: np.ndarray, rmask: np.ndarray, river_index: int) -> np.ndarray:
    out = out.copy()
    out[rmask == 2] = river_index
    return out


def interior_mask(land: np.ndarray) -> np.ndarray:
    """Land pixels whose four neighbours are land; the ring left over becomes beach."""
    inner = np.zeros_like(land)
    inner[1:-1, 1:-1] = (land[1:-1, 1:-1] & land[:-2, 1:-1] & land[2:, 1:-1]
                         & land[1:-1, :-2] & land[1:-1, 2:])
    return inner


# ---------------------------------------------------------------- previews
def preview_colours(n: int) -> np.ndarray:
    """A fixed, well separated colour per palette index (index 0 = deep blue water)."""
    rng = np.random.default_rng(7)
    cols = np.zeros((max(n, 1), 3), dtype=np.uint8)
    cols[0] = (28, 60, 120)
    hues = np.linspace(0, 1, max(n - 1, 1), endpoint=False)
    for i, h in enumerate(hues, start=1):
        if i >= n:
            break
        s, v = 0.55 + 0.35 * rng.random(), 0.65 + 0.3 * rng.random()
        k = int(h * 6) % 6
        f = h * 6 - int(h * 6)
        p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
        r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][k]
        cols[i] = (int(r * 255), int(g * 255), int(b * 255))
    return cols


def write_previews(project: Project, biomes: np.ndarray, heightmap_u16: np.ndarray,
                   palette: list[str | None], remap: dict[str, str], max_side: int = 2048) -> None:
    H, W = biomes.shape
    step = max(1, int(np.ceil(max(H, W) / max_side)))
    b_ds = biomes[::step, ::step]
    cols = preview_colours(len(palette))
    rgb = cols[np.clip(b_ds, 0, len(palette) - 1)]
    pv = project.preview_dir
    Image.fromarray(rgb).save(pv / "biomes.png")
    h_ds = heightmap_u16[::step, ::step].astype(np.float32)
    lo, hi = float(h_ds.min()), float(max(h_ds.max(), h_ds.min() + 1))
    grey = np.clip((h_ds - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(grey).save(pv / "heightmap.png")
    bb = project.bbox
    legend = {str(i): {"paint": palette[i], "final": remap.get(palette[i], palette[i]),
                       "rgb": cols[i].tolist()} for i in range(1, len(palette))}
    (pv / "meta.json").write_text(json.dumps({
        "bbox": {"north": bb.north, "south": bb.south, "west": bb.west, "east": bb.east},
        "step": step, "width": int(b_ds.shape[1]), "height": int(b_ds.shape[0]),
        "full_width": W, "full_height": H, "legend": legend,
    }, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- run
def run(project: Project, reporter: Reporter) -> None:
    reporter.set_step("biomes")
    s = project.scale
    settings = project.biomes
    hm = np.asarray(Image.open(project.heightmap_png)).astype(np.uint16)
    if hm.ndim == 3:
        hm = hm[:, :, 0]
    H, W = hm.shape
    sea = np.asarray(Image.open(project.sea_mask_png))
    if sea.ndim == 3:
        sea = sea[:, :, 0]
    climate = np.asarray(Image.open(project.climate_png)).astype(np.uint8)
    if climate.ndim == 3:
        climate = climate[:, :, 0]
    if sea.shape != hm.shape or climate.shape != hm.shape:
        raise ValueError(f"raster size mismatch: heightmap {hm.shape}, sea {sea.shape}, climate {climate.shape}")
    reporter.log(f"heightmap {W}x{H}")

    # Only climate codes present on land in this bbox need palette slots, which keeps
    # placeholders free for modded biomes. Bands keep their full climate filters.
    land_full = sea < 128
    present = {KG_CODES.get(int(v), "") for v in np.unique(climate[land_full])}
    settings = settings.model_copy(deep=True)
    settings.rules = {code: rule for code, rule in settings.rules.items() if code in present}
    settings.elevation_bands = [b for b in settings.elevation_bands
                                if not b.climates or any(c in present or any(p.startswith(c) for p in present)
                                                         for c in b.climates)]
    reporter.log(f"climate codes on land: {', '.join(sorted(c for c in present if c)) or 'none'}")
    palette, remap = build_palette(settings)
    index_of = index_map(settings, palette, remap)
    land = interior_mask(land_full) if settings.coast_ring else land_full

    C = coarse_noise(H, W, settings.noise_cell, settings.seed)
    out = np.zeros((H, W), dtype=np.uint8)
    B = 2048
    for r0 in range(0, H, B):
        reporter.check_cancel()
        r1 = min(H, r0 + B)
        Y = geo.u16_to_y(hm[r0:r1], s.build_low, s.build_high)
        elev = geo.elevation_from_y(Y, s.water_level, s.vertical_exaggeration, s.meters_per_block)
        n = noise_rows(C, settings.noise_cell, r0, r1, W)
        out[r0:r1] = classify(climate[r0:r1], elev, n, land[r0:r1], settings, index_of)
        reporter.progress(r1 / H)

    if project.rivers_mask_png.exists():
        rmask = np.asarray(Image.open(project.rivers_mask_png))
        if rmask.ndim == 3:
            rmask = rmask[:, :, 0]
        ridx = index_of.get(settings.river_biome)
        if ridx is not None and rmask.shape == out.shape:
            out = apply_river_overlay(out, rmask, ridx)
            reporter.log(f"painted {settings.river_biome} on {int((rmask == 2).sum())} flooded px")

    Image.fromarray(out).save(project.biomes_png)
    project.biomes_palette_json.write_text(
        json.dumps({str(i): palette[i] for i in range(len(palette)) if palette[i]}, indent=2),
        encoding="utf-8")
    project.biomes_remap_json.write_text(json.dumps(remap, indent=2), encoding="utf-8")
    write_previews(project, out, hm, palette, remap)

    vals, counts = np.unique(out, return_counts=True)
    tot = float(H * W)
    reporter.log("biome histogram (top 15):")
    for v, c in sorted(zip(vals.tolist(), counts.tolist()), key=lambda t: -t[1])[:15]:
        name = palette[v] if v < len(palette) and palette[v] else "(unpainted: water / coast)"
        final = remap.get(name, name)
        reporter.log(f"  {c:12d}  {c / tot * 100:5.1f}%  idx {v:3d}  {final}")
    reporter.log(f"wrote {project.biomes_png} ({len(palette) - 1} classes, {len(remap)} modded swaps)")
    reporter.progress(1.0)

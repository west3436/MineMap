"""Swap placeholder vanilla biome names for modded ids inside region files.

Only the palette strings in each chunk section change; the packed indices stay as
WorldPainter wrote them, so the painted geography is untouched.
"""
from __future__ import annotations

import gzip
import io
import json
import struct
import zlib
from pathlib import Path

import nbtlib

from ..progress import Reporter
from ..project import Project

SECTOR = 4096
DONE_MARKER = ".remap_done"


def check_remap(remap: dict[str, str]) -> None:
    for k, v in remap.items():
        if not k.startswith("minecraft:"):
            raise ValueError(f"placeholder {k!r} must be a vanilla biome")
        if v.startswith("minecraft:") or ":" not in v:
            raise ValueError(f"target {v!r} must be a namespaced modded biome")


def read_chunks(path: Path):
    """Yield (index, nbt root, compression type) for each chunk in a region file."""
    with open(path, "rb") as f:
        header = f.read(SECTOR)
        if len(header) < SECTOR:
            return
        for i in range(1024):
            entry = struct.unpack_from(">I", header, i * 4)[0]
            off, cnt = entry >> 8, entry & 0xFF
            if off == 0 or cnt == 0:
                continue
            f.seek(off * SECTOR)
            raw_len = struct.unpack(">I", f.read(4))[0]
            comp = f.read(1)[0]
            data = f.read(raw_len - 1)
            if comp == 1:
                nbt_bytes = gzip.decompress(data)
            elif comp == 2:
                nbt_bytes = zlib.decompress(data)
            elif comp == 3:
                nbt_bytes = data
            else:
                raise ValueError(f"unknown compression {comp} in {path} chunk {i}")
            yield i, nbtlib.File.parse(io.BytesIO(nbt_bytes)), comp


def _sections(root):
    secs = root.get("sections")
    if secs is None and "Level" in root:
        secs = root["Level"].get("sections")
    return secs or []


def swap_chunk(root, remap: dict[str, str]) -> int:
    n = 0
    for sec in _sections(root):
        biomes = sec.get("biomes")
        if biomes is None or biomes.get("palette") is None:
            continue
        pal = biomes["palette"]
        for j in range(len(pal)):
            s = str(pal[j])
            if s in remap:
                pal[j] = nbtlib.String(remap[s])
                n += 1
    return n


def write_region(path: Path, chunks_out: dict[int, tuple[int, bytes]], timestamps: bytes | None) -> None:
    locations = bytearray(SECTOR)
    body = bytearray()
    next_sector = 2
    for i in range(1024):
        if i not in chunks_out:
            continue
        comp, payload = chunks_out[i]
        blob = struct.pack(">I", len(payload) + 1) + bytes([comp]) + payload
        blob += b"\x00" * ((-len(blob)) % SECTOR)
        sectors = len(blob) // SECTOR
        struct.pack_into(">I", locations, i * 4, (next_sector << 8) | (sectors & 0xFF))
        body += blob
        next_sector += sectors
    ts = timestamps if (timestamps and len(timestamps) == SECTOR) else bytes(SECTOR)
    with open(path, "wb") as f:
        f.write(locations)
        f.write(ts)
        f.write(body)


def encode_chunk(root) -> tuple[int, bytes]:
    out = io.BytesIO()
    root.write(out)
    return 2, zlib.compress(out.getvalue())


def process_region(path: Path, remap: dict[str, str]) -> int:
    with open(path, "rb") as f:
        f.read(SECTOR)
        timestamps = f.read(SECTOR)
    chunks_out, swapped = {}, 0
    for i, root, _comp in read_chunks(path):
        swapped += swap_chunk(root, remap)
        chunks_out[i] = encode_chunk(root)
    if swapped:
        write_region(path, chunks_out, timestamps)
    return swapped


def swap_region_biomes(region_dir: Path, remap: dict[str, str], reporter: Reporter | None = None) -> int:
    check_remap(remap)
    files = sorted(Path(region_dir).glob("*.mca"))
    total = 0
    for k, p in enumerate(files):
        if reporter:
            reporter.check_cancel()
        total += process_region(p, remap)
        if reporter:
            reporter.progress((k + 1) / max(1, len(files)))
    return total


def verify_region_biomes(region_dir: Path, remap: dict[str, str]) -> dict[str, int]:
    """Count palette entries that are placeholders or remap targets."""
    counts: dict[str, int] = {}
    targets = set(remap.values())
    for p in sorted(Path(region_dir).glob("*.mca")):
        for _i, root, _c in read_chunks(p):
            for sec in _sections(root):
                b = sec.get("biomes")
                if b is None or b.get("palette") is None:
                    continue
                for nm in b["palette"]:
                    s = str(nm)
                    if s in remap or s in targets:
                        counts[s] = counts.get(s, 0) + 1
    return counts


def run(project: Project, reporter: Reporter) -> None:
    reporter.set_step("remap")
    marker = project.out_dir / DONE_MARKER
    remap: dict[str, str] = {}
    if project.biomes_remap_json.exists():
        remap = json.loads(project.biomes_remap_json.read_text(encoding="utf-8"))
    if not remap:
        reporter.log("no modded biomes in the mapping; nothing to swap")
        marker.write_text("empty\n", encoding="utf-8")
        reporter.progress(1.0)
        return
    region_dir = project.world_dir() / "region"
    if not region_dir.is_dir():
        raise RuntimeError(f"{region_dir} not found; export the world first")
    n = swap_region_biomes(region_dir, remap, reporter)
    reporter.log(f"swapped {n} palette entries for {len(remap)} modded biomes")
    leftover = {k: v for k, v in verify_region_biomes(region_dir, remap).items() if k in remap}
    if leftover:
        reporter.log(f"warning: placeholders still present: {leftover}")
    marker.write_text(json.dumps({"swapped": n}), encoding="utf-8")
    reporter.progress(1.0)

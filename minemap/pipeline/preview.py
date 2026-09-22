"""Map overlay previews. Each preview PNG carries its own bounds file so the GUI can
place it where it was built, whatever the project's box says now."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from ..project import Project

MAX_SIDE = 2048


def downsample_step(h: int, w: int, max_side: int = MAX_SIDE) -> int:
    return max(1, int(np.ceil(max(h, w) / max_side)))


def write_preview(project: Project, name: str, image: np.ndarray, extra: dict | None = None) -> Path:
    """Save out/preview/<name>.png plus <name>.json with the box it covers."""
    pv = project.preview_dir
    Image.fromarray(image).save(pv / f"{name}.png")
    bb = project.bbox
    meta = {"bbox": {"north": bb.north, "south": bb.south, "west": bb.west, "east": bb.east},
            "width": int(image.shape[1]), "height": int(image.shape[0])}
    if extra:
        meta.update(extra)
    (pv / f"{name}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return pv / f"{name}.png"


def heightmap_preview(heightmap_u16: np.ndarray) -> np.ndarray:
    step = downsample_step(*heightmap_u16.shape)
    h_ds = heightmap_u16[::step, ::step].astype(np.float32)
    lo, hi = float(h_ds.min()), float(max(h_ds.max(), h_ds.min() + 1))
    return np.clip((h_ds - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)


def read_meta(project: Project, name: str) -> dict | None:
    f = project.preview_dir / f"{name}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))

"""River centrelines from HydroRIVERS (HydroSHEDS), split into regional shapefiles.

Each region archive is downloaded once into the cache. A bbox that straddles
regions loads every overlapping one. Reaches are filtered by Strahler order and
by intersection with the bbox before they are returned.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import requests

from ..geo import BBox
from ..paths import cache_dir
from ..progress import Reporter

URL = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_{reg}_shp.zip"

# Approximate extents of the HydroSHEDS regions. Overlaps are intentional: loading
# one region too many costs a download, missing one loses rivers.
REGIONS: dict[str, BBox] = {
    "af": BBox(38, -35, -19, 55),
    "ar": BBox(84, 50, -180, -50),
    "as": BBox(61, -12, 57, 180),
    "au": BBox(-9, -56, 95, 180),
    "eu": BBox(82, 12, -32, 70),
    "gr": BBox(84, 59, -75, -10),
    "na": BBox(62, 5, -170, -50),
    "sa": BBox(15, -56, -93, -32),
    "si": BBox(82, 45, 58, 180),
}


def regions_for(bbox: BBox) -> list[str]:
    return [reg for reg, ext in REGIONS.items() if ext.intersects(bbox)]


def _find_shp(d: Path) -> Path | None:
    for p in d.rglob("*.shp"):
        return p
    return None


def ensure_region(reg: str, cache: Path, reporter: Reporter) -> Path:
    d = cache / reg
    d.mkdir(parents=True, exist_ok=True)
    shp = _find_shp(d)
    if shp:
        return shp
    zpath = d / f"HydroRIVERS_v10_{reg}_shp.zip"
    if not zpath.exists():
        reporter.log(f"downloading HydroRIVERS region '{reg}' (one time)")
        r = requests.get(URL.format(reg=reg), timeout=900, stream=True)
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(zpath, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                reporter.check_cancel()
                f.write(chunk)
                got += len(chunk)
                if total:
                    reporter.progress(got / total * 0.3)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(d)
    zpath.unlink(missing_ok=True)
    shp = _find_shp(d)
    if not shp:
        raise RuntimeError(f"no .shp inside the HydroRIVERS '{reg}' archive")
    return shp


def _iter_parts(points, parts):
    idx = list(parts) + [len(points)]
    for i in range(len(idx) - 1):
        seg = points[idx[i]:idx[i + 1]]
        if len(seg) >= 2:
            yield seg


def keep_reach(order: int, shape_bbox, bbox: BBox, min_strahler: int) -> bool:
    if int(order) < min_strahler:
        return False
    xmin, ymin, xmax, ymax = shape_bbox
    return not (xmax < bbox.west or xmin > bbox.east or ymax < bbox.south or ymin > bbox.north)


def load_reaches(bbox: BBox, min_strahler: int, reporter: Reporter,
                 cache: Path | None = None) -> list[tuple[int, list[tuple[float, float]]]]:
    """[(strahler_order, [(lon, lat), ...]), ...] for reaches inside the bbox."""
    import shapefile
    cache = cache or cache_dir("rivers")
    regs = regions_for(bbox)
    if not regs:
        reporter.log("bbox overlaps no HydroRIVERS region; no rivers")
        return []
    reaches: list[tuple[int, list[tuple[float, float]]]] = []
    for k, reg in enumerate(regs):
        shp = ensure_region(reg, cache, reporter)
        sf = shapefile.Reader(str(shp))
        n = len(sf)
        reporter.log(f"scanning {n} reaches in region '{reg}'")
        for i, sr in enumerate(sf.iterShapeRecords()):
            if i % 20000 == 0:
                reporter.check_cancel()
                reporter.progress(0.3 + 0.7 * (k + i / max(1, n)) / len(regs))
            order = int(sr.record["ORD_STRA"])
            if not keep_reach(order, sr.shape.bbox, bbox, min_strahler):
                continue
            for seg in _iter_parts(sr.shape.points, sr.shape.parts):
                reaches.append((order, [(float(x), float(y)) for x, y in seg]))
    reporter.log(f"loaded {len(reaches)} reaches (order >= {min_strahler}) in bbox")
    reporter.progress(1.0)
    return reaches

"""Filesystem locations MineMap uses at runtime.

Cache (DEM tiles, river shapefiles, climate rasters) is shared across projects and
lives under the per-user local app data folder. Each project keeps its own workspace
directory (project.json plus generated rasters, datapacks and logs).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "MineMap"


def cache_dir(kind: str | None = None) -> Path:
    d = app_data_dir() / "cache"
    if kind:
        d = d / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    d = app_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "settings.json"


def default_projects_dir() -> Path:
    d = Path.home() / "MineMap"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource_dir() -> Path:
    """Directory holding bundled package resources (web/, templates), also under PyInstaller."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "minemap"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent

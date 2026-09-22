"""Find modded Minecraft instances on this machine.

Each launcher keeps a different layout, so every reader below is small and defensive:
a missing or malformed file skips that instance with a warning instead of raising.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

LOADER_UIDS = {
    "net.neoforged.neoforge": "neoforge",
    "net.minecraftforge": "forge",
    "net.fabricmc.fabric-loader": "fabric",
    "org.quiltmc.quilt-loader": "quilt",
}
LOADER_WORDS = ("neoforge", "forge", "fabric", "quilt")


@dataclass
class InstanceInfo:
    launcher: str
    name: str
    game_dir: str
    loader: str = ""
    mc_version: str = ""
    vanilla_jar: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------- helpers
def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _loader_from_text(s: str) -> str:
    s = s.lower()
    for w in LOADER_WORDS:
        if w in s:
            return w
    return ""


def _find_jar(*candidates: Path) -> str:
    for c in candidates:
        if c.is_file():
            return str(c)
    return ""


def _versions_dir_guess(game_dir: Path) -> tuple[str, str]:
    """Loader and MC version from a vanilla-style versions/ folder, newest first."""
    vdir = game_dir / "versions"
    if not vdir.is_dir():
        return "", ""
    entries = sorted(vdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    loader, mc = "", ""
    for d in entries:
        j = _read_json(d / f"{d.name}.json")
        if not j:
            continue
        ld = _loader_from_text(d.name)
        base = str(j.get("inheritsFrom") or "")
        if ld and not loader:
            loader = ld
            mc = base or mc
        elif not ld and not mc and re.match(r"^\d+\.\d+", d.name):
            mc = d.name
        if loader and mc:
            break
    return loader, mc


def _loader_from_mods(game_dir: Path) -> str:
    """Peek at a few mod jars for their loader descriptor when nothing else says."""
    mods = game_dir / "mods"
    if not mods.is_dir():
        return ""
    for jar in sorted(mods.glob("*.jar"))[:10]:
        try:
            with zipfile.ZipFile(jar) as z:
                names = set(z.namelist())
        except Exception:
            continue
        if "META-INF/neoforge.mods.toml" in names:
            return "neoforge"
        if "META-INF/mods.toml" in names:
            return "forge"
        if "quilt.mod.json" in names:
            return "quilt"
        if "fabric.mod.json" in names:
            return "fabric"
    return ""


def _appdata() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


# ----------------------------------------------------------------------------- MultiMC family
def _mmc_instances(root: Path, launcher: str) -> list[InstanceInfo]:
    out: list[InstanceInfo] = []
    inst_root = root / "instances"
    if not inst_root.is_dir():
        return out
    for d in sorted(inst_root.iterdir()):
        if not d.is_dir():
            continue
        pack = _read_json(d / "mmc-pack.json")
        if pack is None:
            continue
        game_dir = d / ".minecraft"
        if not game_dir.is_dir():
            game_dir = d / "minecraft"
        if not game_dir.is_dir():
            continue
        name = d.name
        cfg = d / "instance.cfg"
        if cfg.is_file():
            try:
                for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("name="):
                        name = line[5:].strip() or name
            except Exception:
                pass
        loader, mc = "", ""
        warnings: list[str] = []
        for comp in pack.get("components", []) or []:
            uid = str(comp.get("uid", ""))
            ver = str(comp.get("version") or comp.get("cachedVersion") or "")
            if uid == "net.minecraft":
                mc = ver
            elif uid in LOADER_UIDS:
                loader = LOADER_UIDS[uid]
        if not mc:
            warnings.append(f"{name}: mmc-pack.json has no net.minecraft component")
        jar = _find_jar(root / "libraries" / "com" / "mojang" / "minecraft" / mc / f"minecraft-{mc}-client.jar") if mc else ""
        out.append(InstanceInfo(launcher, name, str(game_dir), loader or "vanilla", mc, jar, warnings))
    return out


def _mmc_roots() -> list[tuple[Path, str]]:
    ad = _appdata()
    roots = [(ad / "PrismLauncher", "prism"), (ad / "PolyMC", "polymc"), (ad / "MultiMC", "multimc")]
    if sys.platform == "darwin":
        roots.append((Path.home() / "Library" / "Application Support" / "PrismLauncher", "prism"))
    for base in (Path.home(), Path.home() / "Desktop", Path.home() / "Downloads", Path("C:/"), Path("D:/")):
        for nm in ("MultiMC", "PrismLauncher", "PolyMC"):
            p = base / nm
            if (p / "instances").is_dir():
                roots.append((p, nm.lower().replace("launcher", "")))
    seen, uniq = set(), []
    for r, l in roots:
        key = str(r).lower()
        if key not in seen and r.is_dir():
            seen.add(key)
            uniq.append((r, l))
    return uniq


# ----------------------------------------------------------------------------- CurseForge
def _curseforge_instances() -> list[InstanceInfo]:
    out: list[InstanceInfo] = []
    roots = [Path.home() / "curseforge" / "minecraft", Path.home() / "Documents" / "curseforge" / "minecraft"]
    for root in roots:
        inst_root = root / "Instances"
        if not inst_root.is_dir():
            continue
        for d in sorted(inst_root.iterdir()):
            meta = _read_json(d / "minecraftinstance.json")
            if meta is None:
                continue
            name = str(meta.get("name") or d.name)
            mc = str(meta.get("gameVersion") or "")
            loader_name = str((meta.get("baseModLoader") or {}).get("name") or "")
            loader = _loader_from_text(loader_name) or _loader_from_mods(d) or "vanilla"
            if not mc:
                mc = str((meta.get("baseModLoader") or {}).get("minecraftVersion") or "")
            jar = _find_jar(root / "Install" / "versions" / mc / f"{mc}.jar") if mc else ""
            out.append(InstanceInfo("curseforge", name, str(d), loader, mc, jar))
    return out


# ----------------------------------------------------------------------------- Modrinth App
def _modrinth_db_profiles(root: Path) -> dict[str, dict]:
    """Newer Modrinth App versions keep profiles in app.db (sqlite)."""
    db = root / "app.db"
    if not db.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(profiles)")]
            if not cols:
                return {}
            rows = con.execute("SELECT * FROM profiles").fetchall()
        finally:
            con.close()
    except Exception:
        return {}
    res = {}
    for row in rows:
        rec = dict(zip(cols, row))
        key = str(rec.get("path") or rec.get("name") or "")
        if key:
            res[key] = rec
    return res


def _modrinth_instances() -> list[InstanceInfo]:
    out: list[InstanceInfo] = []
    ad = _appdata()
    for root in (ad / "ModrinthApp", ad / "com.modrinth.theseus"):
        prof_root = root / "profiles"
        if not prof_root.is_dir():
            continue
        db = _modrinth_db_profiles(root)
        for d in sorted(prof_root.iterdir()):
            if not d.is_dir():
                continue
            name, loader, mc = d.name, "", ""
            pj = _read_json(d / "profile.json")
            if pj:
                meta = pj.get("metadata") or pj
                name = str(meta.get("name") or name)
                loader = _loader_from_text(str(meta.get("loader") or ""))
                mc = str(meta.get("game_version") or "")
            rec = db.get(d.name)
            if rec:
                name = str(rec.get("name") or name)
                loader = loader or _loader_from_text(str(rec.get("mod_loader") or ""))
                mc = mc or str(rec.get("game_version") or "")
            if not loader:
                loader = _loader_from_mods(d)
            if not mc:
                _, mc = _versions_dir_guess(d)
            jar = _find_jar(root / "meta" / "versions" / mc / f"{mc}.jar") if mc else ""
            out.append(InstanceInfo("modrinth", name, str(d), loader or "vanilla", mc, jar))
    return out


# ----------------------------------------------------------------------------- vanilla launcher
def _vanilla_instances() -> list[InstanceInfo]:
    ad = _appdata()
    game_dir = ad / ".minecraft" if sys.platform != "darwin" else ad / "minecraft"
    if not game_dir.is_dir():
        return []
    loader, mc = _versions_dir_guess(game_dir)
    if not loader:
        loader = _loader_from_mods(game_dir) or "vanilla"
    jar = _find_jar(game_dir / "versions" / mc / f"{mc}.jar") if mc else ""
    return [InstanceInfo("vanilla", ".minecraft", str(game_dir), loader, mc, jar)]


# ----------------------------------------------------------------------------- public API
def detect_instances() -> list[InstanceInfo]:
    found: list[InstanceInfo] = []
    for root, launcher in _mmc_roots():
        try:
            found.extend(_mmc_instances(root, launcher))
        except Exception as e:  # a broken launcher folder must not hide the others
            found.append(InstanceInfo(launcher, str(root), "", warnings=[f"skipped: {e}"]))
    for fn in (_curseforge_instances, _modrinth_instances, _vanilla_instances):
        try:
            found.extend(fn())
        except Exception:
            pass
    return [i for i in found if i.game_dir]


def describe_folder(path: str) -> InstanceInfo:
    """Describe a user-picked folder. Accepts an instance root, a .minecraft dir, or a mods dir."""
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"not a directory: {path}")
    if p.name.lower() == "mods" and p.parent.is_dir():
        p = p.parent
    for sub in (".minecraft", "minecraft"):
        if not (p / "mods").is_dir() and (p / sub / "mods").is_dir():
            p = p / sub
            break
    if not (p / "mods").is_dir() and not (p / "saves").is_dir():
        raise ValueError(f"no mods or saves folder under {path}")
    # reuse the launcher readers when the folder belongs to a known layout
    for root, launcher in _mmc_roots():
        for inst in _mmc_instances(root, launcher):
            if Path(inst.game_dir).resolve() == p.resolve():
                return inst
    loader, mc = _versions_dir_guess(p)
    if not loader:
        loader = _loader_from_mods(p) or "vanilla"
    jar = _find_jar(p / "versions" / mc / f"{mc}.jar") if mc else ""
    if not jar and mc:
        # a MultiMC-style tree keeps the client jar two levels up under libraries/
        jar = _find_jar(p.parent.parent.parent / "libraries" / "com" / "mojang" / "minecraft" / mc / f"minecraft-{mc}-client.jar")
    name = p.parent.name if p.name in (".minecraft", "minecraft") else p.name
    return InstanceInfo("folder", name, str(p), loader, mc, jar)

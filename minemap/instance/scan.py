"""Scan a modded instance for the worldgen content MineMap needs to know about:
biomes (painting targets), structures and structure sets (placer and void pack),
ores (informational), and mod metadata.

Only data-driven content is visible here. Biomes registered in code (TerraBlender
regions, for example) never appear in a jar's data folder, so they cannot be offered.
"""
from __future__ import annotations

import io
import json
import re
import tomllib
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..progress import Reporter
from . import vanilla
from .detect import InstanceInfo

VANILLA_BUNDLED = "vanilla-bundled"

_RE_WORLDGEN = re.compile(r"^data/([^/]+)/worldgen/(biome|structure|structure_set|configured_feature|placed_feature)/(.+)\.json$")
_RE_BIOME_TAG = re.compile(r"^data/([^/]+)/tags/worldgen/biome/(.+)\.json$")
ORE_TYPES = {"minecraft:ore", "minecraft:scattered_ore"}


# ----------------------------------------------------------------------------- dataclasses
@dataclass
class ModInfo:
    id: str
    name: str
    version: str
    file: str


@dataclass
class BiomeDef:
    id: str
    source: str
    has_precipitation: bool = True
    temperature: float = 0.5


@dataclass
class StructureDef:
    id: str
    source: str
    step: str = ""
    biomes: list[str] = field(default_factory=list)
    start_height: dict | None = None
    projection: str | None = None
    terrain_adaptation: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class StructureSetDef:
    id: str
    source: str
    structures: list[tuple[str, int]] = field(default_factory=list)
    spacing: int = 0
    separation: int = 0
    frequency: float = 1.0
    kind: str = "overworld"     # overworld | nether | end | unknown


@dataclass
class OreDef:
    id: str
    source: str
    blocks: list[str] = field(default_factory=list)
    count_per_chunk: float | None = None


@dataclass
class ScanResult:
    game_dir: str
    loader: str
    mc_version: str
    mods: list[ModInfo] = field(default_factory=list)
    biomes: dict[str, BiomeDef] = field(default_factory=dict)
    biome_tags: dict[str, list[str]] = field(default_factory=dict)
    structures: dict[str, StructureDef] = field(default_factory=dict)
    structure_sets: dict[str, StructureSetDef] = field(default_factory=dict)
    ores: list[OreDef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def overworld_biomes(self) -> list[str]:
        excluded = set(self.biome_tags.get("#minecraft:is_nether", [])) | set(self.biome_tags.get("#minecraft:is_end", []))
        return [b for b in self.biomes if b not in excluded]

    def to_json(self) -> dict:
        d = asdict(self)
        d["structure_sets"] = {k: {**asdict(v), "structures": [list(t) for t in v.structures]}
                               for k, v in self.structure_sets.items()}
        return d

    @classmethod
    def from_json(cls, d: dict) -> "ScanResult":
        r = cls(game_dir=d.get("game_dir", ""), loader=d.get("loader", ""), mc_version=d.get("mc_version", ""))
        r.mods = [ModInfo(**m) for m in d.get("mods", [])]
        r.biomes = {k: BiomeDef(**v) for k, v in d.get("biomes", {}).items()}
        r.biome_tags = {k: list(v) for k, v in d.get("biome_tags", {}).items()}
        r.structures = {k: StructureDef(**v) for k, v in d.get("structures", {}).items()}
        r.structure_sets = {}
        for k, v in d.get("structure_sets", {}).items():
            v = dict(v)
            v["structures"] = [(s[0], int(s[1])) for s in v.get("structures", [])]
            r.structure_sets[k] = StructureSetDef(**v)
        r.ores = [OreDef(**o) for o in d.get("ores", [])]
        r.warnings = list(d.get("warnings", []))
        return r


# ----------------------------------------------------------------------------- parsing helpers
def loads_lenient(raw: bytes | str):
    """Parse JSON the way GSON does: tolerate comments and trailing commas."""
    s = raw.decode("utf-8-sig", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^[ \t]*//.*$", "", s)
    s = re.sub(r"(?m)(?<=[,{\[\s])//[^\"\n]*$", "", s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return json.loads(s)


def _tag_values(obj) -> list[str]:
    vals = obj.get("values", []) if isinstance(obj, dict) else obj
    out = []
    for v in vals or []:
        if isinstance(v, dict):
            v = v.get("id")
        if isinstance(v, str):
            out.append(v)
    return out


def _as_list(x) -> list[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return [str(v) for v in x]


def _mod_info_from_jar(z: zipfile.ZipFile, file: str) -> ModInfo | None:
    names = set(z.namelist())
    if "fabric.mod.json" in names:
        try:
            j = loads_lenient(z.read("fabric.mod.json"))
            return ModInfo(str(j.get("id", "")), str(j.get("name") or j.get("id", "")), str(j.get("version", "")), file)
        except Exception:
            pass
    if "quilt.mod.json" in names:
        try:
            j = loads_lenient(z.read("quilt.mod.json")).get("quilt_loader", {})
            meta = j.get("metadata", {})
            return ModInfo(str(j.get("id", "")), str(meta.get("name") or j.get("id", "")), str(j.get("version", "")), file)
        except Exception:
            pass
    for toml_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        if toml_name not in names:
            continue
        text = z.read(toml_name).decode("utf-8-sig", "replace")
        try:
            t = tomllib.loads(text)
            mods = t.get("mods") or []
            if mods:
                m = mods[0]
                return ModInfo(str(m.get("modId", "")), str(m.get("displayName") or m.get("modId", "")), str(m.get("version", "")), file)
        except Exception:
            pass
        # broken TOML (unescaped strings are common): regex the three fields we need
        def grab(key):
            m = re.search(r'(?m)^\s*%s\s*=\s*"([^"\n]*)"' % key, text)
            return m.group(1) if m else ""
        mid = grab("modId")
        if mid:
            return ModInfo(mid, grab("displayName") or mid, grab("version"), file)
    return None


# ----------------------------------------------------------------------------- collector
class _Collector:
    def __init__(self):
        self.biomes: dict[str, BiomeDef] = {}
        self.raw_tags: dict[str, list[str]] = {}
        self.structures: dict[str, StructureDef] = {}
        self.structure_sets: dict[str, StructureSetDef] = {}
        self.configured: dict[str, tuple[str, dict]] = {}
        self.placed: dict[str, list[dict]] = {}
        self.warnings: list[str] = []

    def add_zip(self, z: zipfile.ZipFile, source: str) -> None:
        for n in z.namelist():
            m = _RE_WORLDGEN.match(n)
            if m:
                ns, kind, path = m.groups()
                rid = f"{ns}:{path}"
                try:
                    obj = loads_lenient(z.read(n))
                except Exception as e:
                    self.warnings.append(f"{source}: unreadable {n}: {e}")
                    continue
                if not isinstance(obj, dict):
                    continue
                self._add(kind, rid, obj, source)
                continue
            m = _RE_BIOME_TAG.match(n)
            if m:
                ns, path = m.groups()
                try:
                    obj = loads_lenient(z.read(n))
                except Exception as e:
                    self.warnings.append(f"{source}: unreadable {n}: {e}")
                    continue
                tag = f"#{ns}:{path}"
                vals = _tag_values(obj)
                if isinstance(obj, dict) and obj.get("replace"):
                    self.raw_tags[tag] = vals
                else:
                    self.raw_tags.setdefault(tag, []).extend(v for v in vals if v not in self.raw_tags.get(tag, []))

    def _add(self, kind: str, rid: str, obj: dict, source: str) -> None:
        if kind == "biome":
            self.biomes[rid] = BiomeDef(rid, source, bool(obj.get("has_precipitation", True)), float(obj.get("temperature", 0.5) or 0.0))
        elif kind == "structure":
            self.structures[rid] = StructureDef(
                rid, source, str(obj.get("step", "")), _as_list(obj.get("biomes")),
                obj.get("start_height") if isinstance(obj.get("start_height"), dict) else None,
                obj.get("project_start_to_heightmap"), str(obj.get("terrain_adaptation") or ""), obj)
        elif kind == "structure_set":
            p = obj.get("placement") or {}
            structs = []
            for s in obj.get("structures") or []:
                if isinstance(s, dict) and "structure" in s:
                    structs.append((str(s["structure"]), int(s.get("weight", 1) or 1)))
            self.structure_sets[rid] = StructureSetDef(rid, source, structs, int(p.get("spacing") or 0),
                                                       int(p.get("separation") or 0), float(p.get("frequency", 1.0) or 1.0))
        elif kind == "configured_feature":
            self.configured[rid] = (source, obj)
        elif kind == "placed_feature":
            feat = obj.get("feature")
            if isinstance(feat, str):
                self.placed.setdefault(feat, []).append(obj)

    def add_bundled_vanilla(self) -> None:
        for bid, v in vanilla.BIOMES.items():
            self.biomes.setdefault(bid, BiomeDef(bid, VANILLA_BUNDLED, v["has_precipitation"], v["temperature"]))
        for tag, vals in vanilla.BIOME_TAGS.items():
            self.raw_tags.setdefault("#" + tag, list(vals))
        for sid, obj in vanilla.STRUCTURES.items():
            if sid not in self.structures:
                self._add("structure", sid, obj, VANILLA_BUNDLED)
        for ssid, s in vanilla.STRUCTURE_SETS.items():
            if ssid not in self.structure_sets:
                self.structure_sets[ssid] = StructureSetDef(
                    ssid, VANILLA_BUNDLED, [(a, int(b)) for a, b in s["structures"]],
                    int(s.get("spacing") or 0), int(s.get("separation") or 0), float(s.get("frequency") or 1.0))

    # -- resolution ---------------------------------------------------------
    def resolve_tags(self) -> dict[str, list[str]]:
        cache: dict[str, list[str]] = {}

        def resolve(tag: str, stack: set[str]) -> list[str]:
            if tag in cache:
                return cache[tag]
            if tag in stack:
                return []
            stack.add(tag)
            out: list[str] = []
            for v in self.raw_tags.get(tag, []):
                if v.startswith("#"):
                    for b in resolve(v, stack):
                        if b not in out:
                            out.append(b)
                elif v not in out:
                    out.append(v)
            stack.discard(tag)
            cache[tag] = out
            return out

        return {t: resolve(t, set()) for t in self.raw_tags}

    def ores(self) -> list[OreDef]:
        out = []
        for fid, (source, obj) in self.configured.items():
            if obj.get("type") not in ORE_TYPES:
                continue
            cfg = obj.get("config") or {}
            blocks = []
            for t in cfg.get("targets") or []:
                nm = ((t or {}).get("state") or {}).get("Name")
                if isinstance(nm, str) and nm not in blocks:
                    blocks.append(nm)
            count = None
            for pf in self.placed.get(fid, []):
                for pl in pf.get("placement") or []:
                    if isinstance(pl, dict) and pl.get("type") == "minecraft:count":
                        c = pl.get("count")
                        if isinstance(c, (int, float)):
                            count = (count or 0) + float(c)
            out.append(OreDef(fid, source, blocks, count))
        return sorted(out, key=lambda o: o.id)

    def classify_sets(self, tags: dict[str, list[str]]) -> None:
        nether = set(tags.get("#minecraft:is_nether", []))
        end = set(tags.get("#minecraft:is_end", []))
        for ss in self.structure_sets.values():
            if ss.id in vanilla.NON_OVERWORLD_SETS:
                ss.kind = "end" if ss.id.split(":")[1].startswith("end") else "nether"
                continue
            kinds = set()
            for sid, _ in ss.structures:
                sd = self.structures.get(sid)
                if sd is None:
                    kinds.add("unknown")
                    continue
                ids = set()
                for b in sd.biomes:
                    ids.update(tags.get(b, []) if b.startswith("#") else [b])
                if not ids:
                    kinds.add("unknown")
                elif ids <= nether:
                    kinds.add("nether")
                elif ids <= end:
                    kinds.add("end")
                else:
                    kinds.add("overworld")
            if kinds == {"nether"}:
                ss.kind = "nether"
            elif kinds == {"end"}:
                ss.kind = "end"
            elif "overworld" in kinds or not kinds:
                ss.kind = "overworld"
            else:
                ss.kind = "unknown"


# ----------------------------------------------------------------------------- public API
def _open_nested(z: zipfile.ZipFile, name: str) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(io.BytesIO(z.read(name)))
    except Exception:
        return None


def scan_instance(info: InstanceInfo, reporter: Reporter | None = None) -> ScanResult:
    rep = reporter or Reporter(on_log=lambda s: None)
    col = _Collector()
    mods: list[ModInfo] = []
    game_dir = Path(info.game_dir)
    jars = sorted((game_dir / "mods").glob("*.jar")) if (game_dir / "mods").is_dir() else []
    if not jars:
        col.warnings.append(f"no mod jars found under {game_dir / 'mods'}")
    for k, jar in enumerate(jars):
        rep.check_cancel()
        try:
            z = zipfile.ZipFile(jar)
        except zipfile.BadZipFile:
            col.warnings.append(f"not a jar: {jar.name}")
            continue
        with z:
            mi = _mod_info_from_jar(z, jar.name)
            mods.append(mi or ModInfo(jar.stem, jar.stem, "", jar.name))
            src = (mi.id if mi and mi.id else jar.stem)
            col.add_zip(z, src)
            for n in z.namelist():
                if n.startswith("META-INF/jarjar/") and n.lower().endswith(".jar"):
                    nz = _open_nested(z, n)
                    if nz is None:
                        continue
                    with nz:
                        nmi = _mod_info_from_jar(nz, f"{jar.name}!{n}")
                        if nmi:
                            mods.append(nmi)
                        col.add_zip(nz, (nmi.id if nmi and nmi.id else Path(n).stem))
        rep.progress((k + 1) / max(1, len(jars)))
    if info.vanilla_jar and Path(info.vanilla_jar).is_file():
        try:
            with zipfile.ZipFile(info.vanilla_jar) as z:
                col.add_zip(z, "minecraft")
            rep.log(f"vanilla data from {info.vanilla_jar}")
        except Exception as e:
            col.warnings.append(f"vanilla jar unreadable, using bundled tables: {e}")
    else:
        col.warnings.append("vanilla client jar not found, using bundled 1.21.1 tables")
    col.add_bundled_vanilla()
    tags = col.resolve_tags()
    col.classify_sets(tags)
    res = ScanResult(str(game_dir), info.loader, info.mc_version, mods, col.biomes, tags,
                     col.structures, col.structure_sets, col.ores(), col.warnings)
    rep.log(f"scan: {len(mods)} mods, {len(res.biomes)} biomes, {len(res.structures)} structures, "
            f"{len(res.structure_sets)} structure sets, {len(res.ores)} ore features")
    return res

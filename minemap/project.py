"""Project configuration model. One JSON file per project (project.json in the
workspace). Every pipeline step reads its settings from here and nothing else, so
the GUI, the CLI and the tests all drive the same code the same way.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .geo import BBox, Grid, make_grid


class BBoxModel(BaseModel):
    north: float = 36.5
    south: float = 35.0
    west: float = 138.0
    east: float = 140.0

    def to_bbox(self) -> BBox:
        return BBox(self.north, self.south, self.west, self.east)


class ScaleSettings(BaseModel):
    meters_per_block: float = 100.0
    vertical_exaggeration: float = 4.0     # one block = meters_per_block / this, in metres
    water_level: int = 62
    y_min: int = 20                        # clamp floor for baked terrain (keeps seabed above build low)
    y_max: int = 319
    build_low: int = -64
    build_high: int = 320
    deseam: bool = True                    # light vertical blur that hides DEM dataset seams
    zoom_override: int | None = None       # force a Terrarium zoom instead of auto


class RiverSettings(BaseModel):
    enabled: bool = True
    min_strahler: int = 4
    width_by_order: dict[int, int] = Field(default_factory=lambda: {4: 4, 5: 7, 6: 10})
    width_max: int = 14
    flood_threshold_y: int = 64
    channel_depth: int = 4
    dry_depth: int = 6
    carve_max_m: float = 800.0
    bank_blur_px: float = 1.5
    wall_slope: float = 1.0


class WeightedBiome(BaseModel):
    biome: str                             # namespaced id, e.g. minecraft:forest
    weight: float = 1.0


class ElevationBand(BaseModel):
    """Above min_m metres, land in the listed climate codes (or all when empty)
    becomes biome. Bands are evaluated highest first."""
    min_m: float
    biome: str
    climates: list[str] = Field(default_factory=list)   # KG codes ("Dfb") or groups ("D")


class BiomeSettings(BaseModel):
    # Koppen-Geiger code -> weighted biome choices for lowland pixels of that class.
    rules: dict[str, list[WeightedBiome]] = Field(default_factory=dict)
    elevation_bands: list[ElevationBand] = Field(default_factory=list)
    river_biome: str = "minecraft:river"
    coast_ring: bool = True                # leave a 1px ring unpainted so WorldPainter makes beaches
    noise_cell: int = 24                   # blocks per noise cell for patchy biome mixing
    seed: int = 1234
    fill_unknown: str = "minecraft:plains" # for land pixels whose climate code has no rule


class StructureEntry(BaseModel):
    id: str                                # structure id, e.g. minecraft:village_plains
    enabled: bool = True
    count: int = 10
    large: bool = False                    # wider forceload box, flatter placement bias
    placement: Literal["auto", "ocean", "ocean_deep", "coast", "land", "any_land"] = "auto"
    # explicit biome ids the structure may sit on; empty = resolved from the structure def tags
    biomes: list[str] = Field(default_factory=list)
    note: str = ""


class StructureSettings(BaseModel):
    enabled: bool = True
    entries: list[StructureEntry] = Field(default_factory=list)
    density: float = 1.0                   # multiplies every count
    waves: int = 8
    batch: int = 3
    stride: int = 8                        # raster sampling stride in blocks
    seed: int = 1600
    namespace: str = "minemap_structures"


class CaveSettings(BaseModel):
    level: int = 0                         # WorldPainter caves-everywhere level, 0 = off
    min_y: int = -59
    max_y: int = 140
    water_y: int = -20
    surface_breaking: bool = True


class WorldSettings(BaseModel):
    name: str = "MineMap World"
    output_dir: str = ""                   # where the world folder is written; empty = instance saves
    spawn_lat: float | None = None
    spawn_lon: float | None = None
    spawn_y: int = 64
    vanilla_border: bool = True            # square backstop border in level.dat
    void_outside: bool = True              # void_outside datapack: nothing generates beyond the map
    map_format: str = "org.pepsoft.anvil.1.20.5"


class ToolSettings(BaseModel):
    wpscript_path: str = ""                # empty = auto-detect
    java_xmx_gb: int = 8


class InstanceSettings(BaseModel):
    path: str = ""                         # the .minecraft (game) directory
    launcher: str = ""
    name: str = ""
    loader: str = ""
    mc_version: str = ""


class Project(BaseModel):
    version: int = 1
    name: str = "New map"
    workspace: str = ""                    # directory holding project.json and out/
    bbox: BBoxModel = Field(default_factory=BBoxModel)
    scale: ScaleSettings = Field(default_factory=ScaleSettings)
    rivers: RiverSettings = Field(default_factory=RiverSettings)
    biomes: BiomeSettings = Field(default_factory=BiomeSettings)
    structures: StructureSettings = Field(default_factory=StructureSettings)
    caves: CaveSettings = Field(default_factory=CaveSettings)
    world: WorldSettings = Field(default_factory=WorldSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    instance: InstanceSettings = Field(default_factory=InstanceSettings)

    # ---- derived helpers -------------------------------------------------
    def grid(self) -> Grid:
        return make_grid(self.bbox.to_bbox(), self.scale.meters_per_block)

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace)

    @property
    def out_dir(self) -> Path:
        d = self.workspace_path / "out"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # canonical intermediate files -----------------------------------------
    @property
    def heightmap_png(self) -> Path:
        return self.out_dir / "heightmap.png"

    @property
    def heightmap_orig_png(self) -> Path:
        return self.out_dir / "heightmap.orig.png"

    @property
    def sea_mask_png(self) -> Path:
        return self.out_dir / "sea_mask.png"

    @property
    def rivers_mask_png(self) -> Path:
        return self.out_dir / "rivers_mask.png"

    @property
    def climate_png(self) -> Path:
        return self.out_dir / "climate.png"

    @property
    def biomes_png(self) -> Path:
        return self.out_dir / "biomes.png"

    @property
    def biomes_palette_json(self) -> Path:
        return self.out_dir / "biomes_palette.json"

    @property
    def biomes_remap_json(self) -> Path:
        return self.out_dir / "biomes_remap.json"

    @property
    def scan_json(self) -> Path:
        return self.out_dir / "instance_scan.json"

    @property
    def preview_dir(self) -> Path:
        d = self.out_dir / "preview"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def world_output_dir(self) -> Path:
        """Directory that will contain the world folder after export."""
        if self.world.output_dir:
            return Path(self.world.output_dir)
        if self.instance.path:
            return Path(self.instance.path) / "saves"
        return self.workspace_path / "world"

    def world_dir(self) -> Path:
        return self.world_output_dir() / self.world.name

    # persistence ------------------------------------------------------------
    @property
    def project_file(self) -> Path:
        return self.workspace_path / "project.json"

    def save(self, path: Path | None = None) -> Path:
        p = path or self.project_file
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.model_dump(mode="json"), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Path) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        proj = cls.model_validate(data)
        if not proj.workspace:
            proj.workspace = str(Path(path).parent)
        return proj


def new_project(name: str, workspace: Path) -> Project:
    from .pipeline.defaults import default_biome_settings, default_structure_entries
    p = Project(name=name, workspace=str(workspace))
    p.biomes = default_biome_settings()
    p.structures.entries = default_structure_entries()
    return p

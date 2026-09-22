# MineMap architecture

MineMap turns a real-world bounding box into a Minecraft world that modded worldgen
still decorates: ores, crops and features generate on first load, and structures are
placed by a generated datapack. The pipeline is Python; the world export is driven
through a headless WorldPainter (wpscript). The GUI is a local web app (FastAPI plus a
Leaflet map in the browser).

## Writing rules for every contributor

* No em dashes anywhere (code, comments, docs, UI strings). Use a comma, a period,
  or parentheses instead.
* No filler phrases ("delve", "seamlessly", "robust", "leverage", "it is worth
  noting", "in summary"). Plain, direct sentences.
* UI: prefer an icon with a hover tooltip (title attribute) over a text label.
  Every icon must carry a tooltip that says what it does.
* Comments explain why, not what. Keep them short.

## Package layout

```
minemap/
  __init__.py
  __main__.py            CLI entry: `python -m minemap` starts the server, opens the browser
  paths.py               cache dir, settings path, resource dir (PyInstaller aware)
  progress.py            Reporter (log, progress, cancel) shared by every step
  geo.py                 BBox, Grid, coordinate transforms, zoom picking, Y<->u16
  project.py             Pydantic Project model, saved as project.json
  server.py              FastAPI app, job runner, SSE log stream
  instance/
    detect.py            find launcher instances on this machine
    scan.py              scan an instance: mods, biomes, structures, structure sets, ores
    vanilla.py           bundled fallback lists for vanilla 1.21 biomes, structures, tags
  data/
    dem.py               Terrarium DEM tiles -> elevation array for a Grid
    rivers.py            HydroRIVERS download (per region) -> reaches inside a bbox
    climate.py           Koppen-Geiger raster download -> class array for a Grid
    kg_classes.json      code -> name/colour table
  pipeline/
    defaults.py          default biome rules, elevation bands, vanilla structure table
    heightmap.py         elevation -> baked Y -> heightmap.png + sea_mask.png
    rivers.py            carve rivers into heightmap.png, write rivers_mask.png
    biomes.py            climate + elevation + noise -> biomes.png, palette, remap
    worldpainter.py      locate wpscript, render JS template, run export
    templates/export.js  WorldPainter script template
    level_meta.py        level.dat spawn, border, generator
    remap.py             region palette rewrite for modded biomes
    void_pack.py         void_outside datapack (noise settings + structure_set disables)
    structures.py        structure placer datapack
    runner.py            ordered step list, run_all / run_step
  web/
    index.html, app.js, style.css, icons.js
tests/
docs/
```

## Data flow

```
Project (project.json)
   |
   v
[1] climate     data/climate.py  -> out/climate.png          (uint8 KG code per px)
[2] heightmap   data/dem.py + pipeline/heightmap.py
                                 -> out/heightmap.png        (16-bit, Y mapped over build range)
                                    out/sea_mask.png         (255 = sea)
[3] rivers      data/rivers.py + pipeline/rivers.py
                                 -> out/heightmap.png (carved), out/heightmap.orig.png, out/rivers_mask.png
[4] biomes      pipeline/biomes.py
                                 -> out/biomes.png           (uint8 palette index, 0 = leave to WorldPainter)
                                    out/biomes_palette.json  {"1": "minecraft:forest", ...}  (vanilla names painted)
                                    out/biomes_remap.json    {"minecraft:dark_forest": "mod:biome", ...}
[5] export      pipeline/worldpainter.py -> <output>/<world name>/   (region files, level.dat)
[6] level       pipeline/level_meta.py   -> spawn, border, generator in level.dat
[7] remap       pipeline/remap.py        -> modded biome names swapped into region palettes
[8] void        pipeline/void_pack.py    -> <world>/datapacks/minemap_void/
[9] structures  pipeline/structures.py   -> <world>/datapacks/minemap_structures/ + commands.txt
```

Steps [1] and [2] are independent. Every step is a function
`run(project: Project, reporter: Reporter) -> None` in its module, plus smaller pure
functions that tests can call without network or files.

## Shared conventions

* Raster shape is `(grid.height, grid.width)`, row 0 north, col 0 west.
* `Grid` (geo.py) is the only source of shifts, radii, lon/lat <-> px <-> world.
* Heightmap PNG: mode `I;16`, value = `y_to_u16(Y, build_low, build_high)`.
* Sea mask PNG: mode `L`, 255 where DEM elevation <= 0.
* Biome index 0 means "unpainted": WorldPainter fills ocean, river, beach automatically.
* Placeholder swap: WorldPainter can only paint vanilla biomes. A modded biome is
  painted as an unused vanilla biome and swapped in the region files afterwards.
  `biomes_palette.json` lists what WorldPainter paints; `biomes_remap.json` maps
  placeholder -> final. Both are written by pipeline/biomes.py.
* All long work accepts a `Reporter`; call `reporter.check_cancel()` inside loops.
* Cache: `paths.cache_dir("dem")`, `paths.cache_dir("rivers")`, `paths.cache_dir("climate")`.

## Module interfaces

### instance/detect.py
```python
@dataclass
class InstanceInfo:
    launcher: str          # "prism" | "polymc" | "multimc" | "curseforge" | "modrinth" | "vanilla" | "folder"
    name: str
    game_dir: str          # the directory holding mods/, saves/, config/
    loader: str            # "neoforge" | "forge" | "fabric" | "quilt" | "vanilla" | ""
    mc_version: str
    vanilla_jar: str       # path to the client jar if found, else ""

def detect_instances() -> list[InstanceInfo]
def describe_folder(path: str) -> InstanceInfo       # for a user-picked folder; raises ValueError if no mods dir and no saves dir
```
Search locations (Windows first, then macOS/Linux equivalents):
Prism `%APPDATA%/PrismLauncher/instances/*`, PolyMC `%APPDATA%/PolyMC/instances/*`,
MultiMC (portable, look for `MultiMC/instances` in common dirs), CurseForge
`%USERPROFILE%/curseforge/minecraft/Instances/*` (minecraftinstance.json), Modrinth App
`%APPDATA%/ModrinthApp/profiles/*` and `%APPDATA%/com.modrinth.theseus/profiles/*`,
vanilla `%APPDATA%/.minecraft`. Loader and version from mmc-pack.json,
minecraftinstance.json, profile.json, or `versions/*/*.json`. Vanilla client jar:
Prism-family `<launcher>/libraries/com/mojang/minecraft/<ver>/minecraft-<ver>-client.jar`,
vanilla and CurseForge `.minecraft/versions/<ver>/<ver>.jar`, Modrinth
`<app>/meta/versions/<ver>/<ver>.jar` (search both, tolerate absence).

### instance/scan.py
```python
@dataclass
class ModInfo: id: str; name: str; version: str; file: str
@dataclass
class BiomeDef: id: str; source: str; has_precipitation: bool; temperature: float
@dataclass
class StructureDef:
    id: str; source: str; step: str; biomes: list[str]   # raw: tag ("#ns:name") or ids
    start_height: dict | None; projection: str | None; terrain_adaptation: str
    raw: dict
@dataclass
class StructureSetDef: id: str; source: str; structures: list[tuple[str, int]]; spacing: int; separation: int; frequency: float; kind: str
@dataclass
class OreDef: id: str; source: str; blocks: list[str]; count_per_chunk: float | None
@dataclass
class ScanResult:
    game_dir: str; loader: str; mc_version: str
    mods: list[ModInfo]
    biomes: dict[str, BiomeDef]               # includes vanilla when the client jar was found
    biome_tags: dict[str, list[str]]          # "#minecraft:is_forest" -> resolved biome ids (recursive)
    structures: dict[str, StructureDef]
    structure_sets: dict[str, StructureSetDef]
    ores: list[OreDef]
    warnings: list[str]
    def overworld_biomes(self) -> list[str]   # ids usable as painting targets
    def to_json(self) -> dict; @classmethod from_json(cls, d) -> ScanResult

def scan_instance(info: InstanceInfo, reporter: Reporter | None = None) -> ScanResult
```
Scan sources: every `mods/*.jar` (also nested jars in `META-INF/jarjar/*.jar`),
`datapacks/`, `saves/*/datapacks/` are NOT scanned (world specific), the vanilla client
jar when present, and the bundled `vanilla.py` fallback when it is not. Parse JSON with a
GSON-lenient loader (comments and trailing commas). Mod metadata from
`fabric.mod.json`, `META-INF/neoforge.mods.toml`, `META-INF/mods.toml` (simple TOML
parse: tomllib). Ores = configured features of type `minecraft:ore` and
`minecraft:scattered_ore`, block ids from `targets[].state.Name`. Biome tags resolve
from `data/*/tags/worldgen/biome/**.json` across all sources with `#tag` recursion.
Biomes whose json lives under the nether or end tag sets (`#minecraft:is_nether`,
`#minecraft:is_end`) are excluded from `overworld_biomes()`.

### data/dem.py
```python
def fetch_elevation(grid: Grid, zoom: int, reporter: Reporter, cache: Path | None = None) -> np.ndarray  # float32 metres, (H, W)
```
Terrarium tiles from `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`,
cached as `<cache>/{z}_{x}_{y}.png`. 404 = sea level. Retry with backoff. Bilinear
resample from the Mercator mosaic to the plate carree grid, row by row, reporting
progress. Memory note in the docstring: mosaic is float32 of tiles*256*256.

### data/rivers.py
```python
REGIONS: dict[str, BBox]   # af, ar, as, au, eu, gr, na, sa, si approximate extents
def regions_for(bbox: BBox) -> list[str]
def load_reaches(bbox: BBox, min_strahler: int, reporter: Reporter, cache: Path | None = None) -> list[tuple[int, list[tuple[float, float]]]]
```
URL `https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_{reg}_shp.zip`.

### data/climate.py
```python
KG_CODES: dict[int, str]   # 1 -> "Af" ... 30 -> "EF"; 0 -> "" (ocean / no data)
def kg_group(code: str) -> str   # "Dfb" -> "D"
def fetch_climate(grid: Grid, reporter: Reporter, cache: Path | None = None) -> np.ndarray   # uint8 KG value per px, nearest neighbour
def ensure_raster(cache: Path | None = None) -> Path   # downloads Beck_KG_V1.zip, extracts Beck_KG_V1_present_0p0083.tif
```
Source: `https://ndownloader.figshare.com/files/12407516` (Beck et al. 2018, 1 km).
GeoTIFF is tiled PackBits, 21600 x 43200, pixel 1/120 degree, origin lon -180 lat 90.
Read only tiles intersecting the bbox with tifffile (`page.decode(data, index)`), see
the verified snippet below. `imagecodecs` is not needed for PackBits.

```python
with tifffile.TiffFile(path) as t:
    p = t.pages[0]; tw, tl = p.tilewidth, p.tilelength
    ncols = (p.imagewidth + tw - 1) // tw
    fh = t.filehandle
    for tr in range(r0 // tl, (r1 - 1) // tl + 1):
        for tc in range(c0 // tw, (c1 - 1) // tw + 1):
            idx = tr * ncols + tc
            fh.seek(p.dataoffsets[idx]); data = fh.read(p.databytecounts[idx])
            arr, _, _ = p.decode(data, idx)
            tile = np.asarray(arr).reshape(tl, tw)
```

### pipeline/heightmap.py
```python
def bake(elev_m: np.ndarray, scale: ScaleSettings) -> np.ndarray   # float32 Y, deseamed if enabled, clamped
def deseam(Y: np.ndarray, passes: int = 2) -> np.ndarray
def run(project: Project, reporter: Reporter) -> None                 # fetch DEM, bake, write heightmap.png + sea_mask.png, delete stale heightmap.orig.png
```

### pipeline/rivers.py
Port of the Japan river carve (rasterize, bank distance, filleted carve, bank blur),
parameterised by `RiverSettings` and `ScaleSettings`. `run()` snapshots
heightmap.orig.png the first time, always carves from the snapshot, writes the carved
heightmap.png and rivers_mask.png (2 = flooded, 1 = dry valley, 0 = none). When
`rivers.enabled` is false, `run()` removes rivers_mask.png if present and returns.

### pipeline/biomes.py
```python
VANILLA_OVERWORLD_BIOMES: list[str]
AUTO_BIOMES: set[str]       # biomes WorldPainter may assign on its own; never use as placeholders
def build_palette(settings: BiomeSettings) -> tuple[list[str | None], dict[str, str]]
    # returns (palette, remap): palette[idx] = vanilla name WorldPainter paints (None at 0),
    # remap = placeholder vanilla name -> modded id. Raises ValueError when the placeholder pool runs out.
def classify(climate: np.ndarray, elev_m: np.ndarray, noise: np.ndarray, land: np.ndarray, settings: BiomeSettings, index_of: dict[str, int]) -> np.ndarray   # uint8 indices
def run(project: Project, reporter: Reporter) -> None
```
Per pixel: elevation bands first (highest min_m first, climate filter by code or
group letter), then the weighted rule for the pixel's KG code using the noise value as
the cumulative-weight pick, then `fill_unknown`. Flooded river pixels get
`river_biome`. Coast ring stays 0 when `coast_ring` is true. Also writes a colour
preview `out/preview/biomes.png` (downsampled to max 2048 px, one colour per biome)
and `out/preview/heightmap.png` (grey) for the map overlay.

### pipeline/worldpainter.py
```python
def find_wpscript(explicit: str = "") -> str | None      # explicit path, PATH, common install dirs, settings.json
def ensure_vmoptions(wpscript: str, xmx_gb: int) -> None   # writes -Xmx<N>g into wpscript.vmoptions next to the exe
def render_script(project: Project) -> str                # fills templates/export.js
def run(project: Project, reporter: Reporter) -> None     # writes out/export.js, runs wpscript, streams stdout to reporter
```
Env vars for the template: MM_HEIGHTMAP, MM_BIOMES, MM_PALETTE, MM_SAVES, MM_WORLD,
MM_SHIFTX, MM_SHIFTY, MM_WATER, MM_LOW, MM_HIGH, MM_FORMAT, MM_CAVES_LEVEL,
MM_CAVE_MIN, MM_CAVE_MAX, MM_CAVE_WATER, MM_CAVE_SURFACE. Populate ON, Resources
layer OFF, biome map painted per palette entry (fromLevel idx -> toLevel biome id).
Delete any existing world folder of the same name before export (WorldPainter refuses
to overwrite), after confirming through the GUI (the server exposes a flag).

### pipeline/level_meta.py
```python
def apply(level_dat: Path, spawn: tuple[int, int, int], border_size: float | None, generator: str = "noise") -> None
def run(project: Project, reporter: Reporter) -> None   # spawn from lat/lon via grid, else 0,y,0; keeps a level.dat.wpbak copy
```

### pipeline/remap.py
Generic port of remap_biomes.py: `swap_region_biomes(region_dir: Path, remap: dict[str, str], reporter) -> int`,
`verify_region_biomes(region_dir, remap) -> dict[str, int]`, `run(project, reporter)`.
Guard: keys must be `minecraft:` names, values must not be `minecraft:` names.

### pipeline/void_pack.py
Writes `<world>/datapacks/minemap_void/` with pack.mcmeta (pack_format 48 for 1.21.1;
pick by mc_version: 1.20.5 to 1.21.1 use 41 to 48, 1.21.2+ use 57 or higher, keep a
small table), `data/minecraft/worldgen/noise_settings/overworld.json` (final density
-1, air, sea level -64) and one empty `structure_set` override for every overworld
structure set found in the scan (vanilla list from vanilla.py when no client jar).
Skips sets whose structures are only nether or end (`kind`).

### pipeline/structures.py
Generic port of place_world_structures.py.
```python
def resolve_mask(entry: StructureEntry, sdef: StructureDef | None, scan: ScanResult, final_names: dict[int, str], B_ds, water, deep, coast) -> np.ndarray
def build_placements(...); def write_datapack(...); def run(project, reporter)
```
Placement class when `entry.placement == "auto"`: resolve the def's `biomes` field
through `scan.biome_tags` to a set of biome ids; if all are ocean biomes use water
(deep water when every id starts with `minecraft:deep_`), if any is a beach use coast,
otherwise the set of painted pixels whose final biome id is in the set; empty
resolution falls back to `land`. Y handling is the float fix from place_common.py
(surface / ocean_floor projections get an override structure pinned to the baked Y).
Also writes `<world>/datapacks/<ns>/README.txt` and `out/commands.txt` listing the
in-game commands (chunky border, pre-gen, structure functions).

### pipeline/runner.py
```python
STEPS: list[tuple[str, str, Callable[[Project, Reporter], None]]]   # (key, label, fn) in order
def run_steps(project: Project, keys: list[str], reporter: Reporter) -> None
def step_status(project: Project) -> dict[str, bool]   # which outputs exist already
```

### server.py
FastAPI app. All state is one current `Project` held in memory and saved on every
change. Endpoints:

```
GET  /                                  index.html
GET  /static/*                          web assets
GET  /api/project                       current project JSON
PUT  /api/project                       replace (validated), saves, returns project + grid estimate
POST /api/project/new     {name, dir?}  new project in <dir or default_projects_dir>/<name>
POST /api/project/open    {path}        load project.json
GET  /api/projects                      list projects under default_projects_dir
GET  /api/estimate                      grid size, zoom, tile count, est. export GB, est. RAM
POST /api/pick            {kind: "dir"|"file"}  tkinter native picker on the server side, returns {path}
GET  /api/instances                     detect_instances()
POST /api/instance/select {path}        describe + scan (runs as a job), writes out/instance_scan.json
GET  /api/scan                          last ScanResult JSON (or 404)
GET  /api/biomes/catalog                painting targets: vanilla + modded overworld biomes, plus KG code table
GET  /api/structures/catalog            defaults merged with scan (id, source, suggested placement)
GET  /api/tools                         wpscript path status, java presence, cache sizes
POST /api/job/start       {steps: [..], overwrite_world: bool}
POST /api/job/cancel
GET  /api/job                           status: running, current step, progress, per-step state
GET  /api/job/events                    SSE stream of {type: log|progress|done|error, ...}
GET  /api/preview/{name}.png            out/preview/<name>.png with bbox in headers
GET  /api/commands                      out/commands.txt
```
Jobs run in a background thread. Only one job at a time.

### web/
Single page. Left: Leaflet map (OpenStreetMap tiles, cdnjs Leaflet 1.9.4) with a
rectangle tool (click two corners, then drag handles), numeric bbox inputs, spawn
marker tool, overlay toggle for heightmap and biome previews. Right: icon tab strip
(instance, scale, biomes, rivers, structures, world, build). Every button is an SVG
icon with a title tooltip. Log console and per-step progress on the build tab.
Icons: inline SVG from Lucide (MIT), stored in icons.js as a name -> path map.
No build step, plain ES modules. Dark and light themes via prefers-color-scheme.

## Testing

`pytest` with no network: unit tests use synthetic arrays and small fake jars built
with zipfile in tmp_path. Network fetchers expose pure helpers (tile maths, region
selection, window maths) that are tested; the fetch functions themselves are not.

## Packaging

`minemap.spec` (PyInstaller, onedir, windowed=False so the console shows the log),
bundles `minemap/web`, `minemap/pipeline/templates`, `minemap/data/*.json`.
GitHub Actions `build.yml`: on tag `v*`, windows-latest, python 3.12, build, zip, release.

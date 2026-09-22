# MineMap

MineMap builds a Minecraft world from a real place. Draw a box on an Earth map, point
the tool at a modded instance, and it produces a WorldPainter export that modded
worldgen still decorates: ores, crops and features generate on first load, modded
biomes are painted from real climate data, and structures are placed by a generated
datapack.

The output is a normal Java Edition world folder plus two datapacks. It works with
NeoForge, Forge and Fabric instances on Minecraft 1.20.5 and newer.

## Requirements

* Windows: the release zip contains `MineMap.exe`, nothing else to install for the
  tool itself. Other platforms: Python 3.11 or newer and `pip install -e .`.
* WorldPainter 2.26.x. The portable zip from worldpainter.net is enough. MineMap drives
  its headless `wpscript` runner, which needs Java 17 or newer on the PATH.
* Internet access for the datasets on first use. Elevation tiles, river shapes and the
  climate raster are cached under your local app data folder and reused.
* RAM: a 20,000 x 20,000 block map needs about 8 GB for the Python steps and a similar
  heap for WorldPainter. The size readout under the map shows the estimate as you draw.

## Quick start

1. Download the latest release zip and extract it.
2. Run `MineMap.exe`. A console opens and your browser lands on `http://127.0.0.1:8765/`.
3. Work through the tabs on the right, top to bottom. Every control has a tooltip.

From source: `python -m minemap` does the same thing.

## Workflow

1. **Instance.** Pick the modded instance. MineMap finds Prism, PolyMC, MultiMC,
   CurseForge, Modrinth App and vanilla launcher instances on its own, or you choose a
   game folder. It scans every mod jar and the vanilla client jar for biomes,
   structures, structure sets, biome tags and ore features. Modded biomes and
   structures found here become available in the later tabs.
2. **Map box.** Draw the box with the box tool (two clicks), drag the corners, or type
   the edges. The readout shows the map size in blocks, the elevation zoom, the tile
   count and the size estimates.
3. **Scale.** Metres per block (100 gives 1:100) and vertical exaggeration. With 100 m
   blocks and exaggeration 4, one block of height is 25 m, so a 3,000 m mountain rises
   120 blocks above sea level.
4. **Biomes.** Every Koppen-Geiger climate class has a list of weighted biomes. The
   noise-mixed pick gives natural patches instead of stripes. Elevation bands override
   the climate pick above a height, optionally filtered by climate group. Modded biomes
   appear in the picker once an instance is scanned.
5. **Rivers.** Real river courses from HydroRIVERS are carved into the terrain.
   Lowland reaches flood into water rivers, upland reaches become dry valleys with
   tapered walls.
6. **Structures.** A table of vanilla and modded structures with a placement class,
   a target count and optional biome restrictions. Counts scale with map area and the
   density slider.
7. **World.** World name, output folder (defaults to the instance saves folder), spawn
   point (set with the flag tool on the map), backstop border, void outside the map,
   WorldPainter location and heap size.
8. **Build.** Tick the steps and press run. The log streams live. Steps can be re-run
   individually, for example only biomes and export after changing the mapping.

## What the build produces

* `<output>/<world name>/`: the exported world with `level.dat` already carrying the
  spawn point and a backstop border.
* `datapacks/minemap_void/`: makes every chunk beyond the map generate as void and
  disables natural overworld structure sets, so nothing floats outside the border.
* `datapacks/<structures namespace>/`: the structure placer with `/function` waves.
* `out/commands.txt` in the project workspace, also shown on the build tab: the in-game
  commands to finish the world.

In-game finish (Creative and Peaceful recommended):

1. Set the rectangular border with ChunkyBorder using the printed radii, then
   `/chunky start` to pre-generate the map so ores and features exist everywhere.
2. Run the structure test function near spawn, check the result, then run the full
   placer. Each wave prints a green message when it is done.

## Data sources

* Elevation: Terrarium tiles from the Mapzen and AWS Open Data terrain tiles
  (`elevation-tiles-prod`), built from SRTM, GMTED and ETOPO1 among others.
* Rivers: HydroRIVERS v1.0 from HydroSHEDS (Lehner and Grill, 2013).
* Climate: Beck, H.E. et al. (2018), Present and future Koppen-Geiger climate
  classification maps at 1 km resolution, Scientific Data 5, 180214.
* Base map in the GUI: OpenStreetMap tiles under the ODbL.

Please respect the licences of these datasets when sharing worlds.

## How the mod compatibility works

* The WorldPainter export sets Populate ON and turns the Resources layer OFF. Minecraft
  then runs the worldgen feature pass on first chunk load, so whichever mods are
  installed at that moment place their ores, crops and decorations.
* WorldPainter can only paint vanilla biomes. Each modded biome is painted as an unused
  vanilla placeholder and MineMap rewrites the biome palettes in the region files
  afterwards. The packed indices are untouched, so the geography is preserved.
* Baked chunks never run the structure step, so structures are placed with
  `/place structure` from a generated datapack. Structures that project onto the
  generator's surface would float above baked terrain; MineMap emits override
  structure definitions pinned to the real surface height.
* TerraBlender and similar mods force the overworld generator at load, so the void
  beyond the map is done with a noise settings override in a datapack rather than in
  `level.dat`.

## Limitations

* Biomes registered only in code (for example TerraBlender regions without data files)
  are invisible to the scanner. Add their ids by hand if you know them.
* Structures need an in-game placement pass. Jigsaw structures work, but the pass takes
  a while on big maps.
* Large boxes need a lot of memory and time. Start with a small area to check the look,
  then scale up.
* Coordinates are plate carree: the map is stretched east to west at high latitudes.

## Development

```
pip install -e ".[dev]"
python -m pytest
python -m minemap --no-browser
```

The design and module interfaces are in `docs/ARCHITECTURE.md`. Tests run without
network access. `pyinstaller minemap.spec` builds the Windows folder.

## License

MIT. See `LICENSE`.

// MineMap WorldPainter export script. Run headless: wpscript export.js
// Every input arrives through MM_* environment variables so one template serves
// every project. Verified against WorldPainter 2.26.3 (Nashorn scripting).
function env(name, dflt) {
    var v = java.lang.System.getenv(name);
    return (v === null || v === '') ? dflt : v;
}
var HEIGHTMAP    = env('MM_HEIGHTMAP', '');
var BIOME_MAP    = env('MM_BIOMES', '');
var PALETTE_JSON = env('MM_PALETTE', '');
var SAVES_DIR    = env('MM_SAVES', '');
var WORLD_NAME   = env('MM_WORLD', 'MineMap World');
var SHIFT_X      = parseInt(env('MM_SHIFTX', '0'), 10);
var SHIFT_Y      = parseInt(env('MM_SHIFTY', '0'), 10);
var WATER_LEVEL  = parseInt(env('MM_WATER', '62'), 10);
var BUILD_LOW    = parseInt(env('MM_LOW', '-64'), 10);
var BUILD_HIGH   = parseInt(env('MM_HIGH', '320'), 10);
var MAP_FORMAT_ID = env('MM_FORMAT', 'org.pepsoft.anvil.1.20.5');

print('heightmap : ' + HEIGHTMAP + '\n');
print('saves dir : ' + SAVES_DIR + '\n');
print('world name: ' + WORLD_NAME + '\n');

var heightMap = wp.getHeightMap().fromFile(HEIGHTMAP).go();
var mapFormat = wp.getMapFormat().withId(MAP_FORMAT_ID).go();

// scale(100) = one heightmap pixel per block. The 16-bit sample spans the whole
// build range, matching geo.y_to_u16 on the Python side.
var world = wp.createWorld()
    .fromHeightMap(heightMap)
    .scale(100)
    .shift(SHIFT_X, SHIFT_Y)
    .fromLevels(0, 65535).toLevels(BUILD_LOW, BUILD_HIGH)
    .withWaterLevel(WATER_LEVEL)
    .withMapFormat(mapFormat)
    .withLowerBuildLimit(BUILD_LOW)
    .withUpperBuildLimit(BUILD_HIGH)
    .go();

try { world.setName(WORLD_NAME); } catch (e) { print('setName unavailable: ' + e + '\n'); }

// Mod compatibility: Minecraft and mods run their feature pass on chunk load
// (Populate ON) and WorldPainter does not lay its own ores (Resources OFF).
var dim = world.getDimension(0);
dim.setPopulate(true);
var Resources = Java.type('org.pepsoft.worldpainter.layers.Resources').INSTANCE;
dim.setLayerSettings(Resources, null);
print('populate=' + dim.isPopulate() + ' resourcesSettings=' + dim.getLayerSettings(Resources) + '\n');

var CAVES_LEVEL = parseInt(env('MM_CAVES_LEVEL', '0'), 10);
if (CAVES_LEVEL > 0) {
    var Caves = Java.type('org.pepsoft.worldpainter.layers.Caves').INSTANCE;
    var CavesSettings = Java.type('org.pepsoft.worldpainter.layers.exporters.CavesExporter$CavesSettings');
    var cs = new CavesSettings();
    cs.setCavesEverywhereLevel(CAVES_LEVEL);
    cs.setMinimumLevel(parseInt(env('MM_CAVE_MIN', '-59'), 10));
    cs.setMaximumLevel(parseInt(env('MM_CAVE_MAX', '140'), 10));
    cs.setWaterLevel(parseInt(env('MM_CAVE_WATER', '-20'), 10));
    cs.setFloodWithLava(true);
    cs.setLeaveWater(true);
    cs.setSurfaceBreaking(env('MM_CAVE_SURFACE', '1') === '1');
    dim.setLayerSettings(Caves, cs);
    print('caves: everywhereLevel=' + CAVES_LEVEL + '\n');
} else {
    print('caves: disabled\n');
}

// Paint the biome index map. Index 0 is skipped so WorldPainter keeps its own
// ocean, river and beach assignment there.
var Biome = Java.type('org.pepsoft.worldpainter.layers.Biome').INSTANCE;
var palText = new java.lang.String(
    java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(PALETTE_JSON)),
    java.nio.charset.StandardCharsets.UTF_8);
var palette = JSON.parse(palText);
var biomeMap = wp.getHeightMap().fromFile(BIOME_MAP).go();
var painted = 0;
for (var key in palette) {
    var idx = parseInt(key, 10);
    var name = palette[key];
    var biomeId;
    try {
        biomeId = wp.getBiomeId().fromWorld(world).withName(name).go();
    } catch (e) {
        print('WARN: getBiomeId failed for ' + name + ': ' + e + '\n');
        continue;
    }
    if (biomeId === null || biomeId < 0) { print('WARN: no biome id for ' + name + '\n'); continue; }
    wp.applyHeightMap(biomeMap)
        .toWorld(world)
        .applyToLayer(Biome)
        .fromLevel(idx)
        .toLevel(biomeId)
        .setAlways()
        .scale(100)
        .shift(SHIFT_X, SHIFT_Y)
        .go();
    painted++;
    print('  biome ' + idx + ' -> ' + name + ' (id ' + biomeId + ')\n');
}
print('painted ' + painted + ' biome classes\n');

wp.exportWorld(world).toDirectory(SAVES_DIR).go();
print('Exported "' + WORLD_NAME + '" to ' + SAVES_DIR + '\n');

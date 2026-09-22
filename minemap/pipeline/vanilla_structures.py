"""Default placement table for the vanilla 1.21 overworld structures.

Counts are tuned for a reference map of about 20k x 20k blocks; structures.run
scales them by the real map area and the project's density knob. Every entry
carries an explicit biome list so placement works even when no vanilla client jar
was found (the scan then has no biome tags to resolve).
"""
from __future__ import annotations

from ..project import StructureEntry

REFERENCE_AREA = 20000 * 20000

_PLAINS = ["minecraft:plains", "minecraft:meadow", "minecraft:sunflower_plains"]
_DESERT = ["minecraft:desert"]
_SAVANNA = ["minecraft:savanna", "minecraft:savanna_plateau", "minecraft:windswept_savanna"]
_SNOWY = ["minecraft:snowy_plains", "minecraft:snowy_taiga", "minecraft:snowy_slopes",
          "minecraft:grove", "minecraft:ice_spikes"]
_TAIGA = ["minecraft:taiga", "minecraft:old_growth_pine_taiga", "minecraft:old_growth_spruce_taiga"]
_JUNGLE = ["minecraft:jungle", "minecraft:bamboo_jungle", "minecraft:sparse_jungle"]
_DARK = ["minecraft:dark_forest"]
_SWAMP = ["minecraft:swamp", "minecraft:mangrove_swamp"]
_BADLANDS = ["minecraft:badlands", "minecraft:eroded_badlands", "minecraft:wooded_badlands"]
_OUTPOST = (_PLAINS + _DESERT + _SAVANNA + _SNOWY + _TAIGA + _JUNGLE
            + ["minecraft:forest", "minecraft:birch_forest", "minecraft:grove"])


def _e(sid, count, placement="auto", biomes=None, large=False, note=""):
    return StructureEntry(id=sid, count=count, placement=placement, large=large,
                          biomes=list(biomes or []), note=note)


DEFAULT_ENTRIES: list[StructureEntry] = [
    _e("minecraft:village_plains", 60, biomes=_PLAINS),
    _e("minecraft:village_desert", 25, biomes=_DESERT),
    _e("minecraft:village_savanna", 25, biomes=_SAVANNA),
    _e("minecraft:village_snowy", 25, biomes=["minecraft:snowy_plains"]),
    _e("minecraft:village_taiga", 25, biomes=["minecraft:taiga"]),
    _e("minecraft:pillager_outpost", 35, biomes=_OUTPOST, large=True),
    _e("minecraft:mansion", 12, biomes=_DARK, large=True),
    _e("minecraft:monument", 24, placement="ocean_deep", large=True),
    _e("minecraft:shipwreck", 55, placement="ocean"),
    _e("minecraft:shipwreck_beached", 35, placement="coast"),
    _e("minecraft:ocean_ruin_cold", 45, placement="ocean", note="self positioning, verify Y in game"),
    _e("minecraft:ocean_ruin_warm", 45, placement="ocean", note="self positioning, verify Y in game"),
    _e("minecraft:buried_treasure", 45, placement="coast", note="self positioning"),
    _e("minecraft:igloo", 28, biomes=_SNOWY),
    _e("minecraft:swamp_hut", 18, biomes=_SWAMP),
    _e("minecraft:desert_pyramid", 20, biomes=_DESERT),
    _e("minecraft:jungle_pyramid", 20, biomes=_JUNGLE),
    _e("minecraft:ruined_portal", 40, placement="land", note="self positioning"),
    _e("minecraft:trail_ruins", 30, biomes=_TAIGA + ["minecraft:snowy_taiga"] + _JUNGLE),
    _e("minecraft:mineshaft", 70, placement="land"),
    _e("minecraft:mineshaft_mesa", 18, biomes=_BADLANDS),
    _e("minecraft:ancient_city", 14, placement="land", large=True),
    _e("minecraft:trial_chambers", 28, placement="land", large=True),
    _e("minecraft:stronghold", 3, placement="land", large=True, note="End portal"),
]

DEFAULT_BY_ID: dict[str, StructureEntry] = {e.id: e for e in DEFAULT_ENTRIES}

# Vanilla ids whose definitions position themselves in code (no data driven
# start_height to override). Always placed by plain id.
SELF_POSITIONING = {
    "minecraft:ancient_city", "minecraft:buried_treasure", "minecraft:ruined_portal",
    "minecraft:ocean_ruin_cold", "minecraft:ocean_ruin_warm",
}

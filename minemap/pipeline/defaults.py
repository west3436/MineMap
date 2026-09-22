"""Default biome rules, elevation bands and structure entries for a new project.

The biome rules map every Koppen-Geiger class to weighted vanilla 1.21 biomes. The
GUI edits these per project; nothing here is consulted after new_project().
"""
from __future__ import annotations

from ..project import BiomeSettings, ElevationBand, StructureEntry, WeightedBiome


def _w(*pairs):
    return [WeightedBiome(biome=f"minecraft:{b}", weight=w) for b, w in pairs]


def default_rules() -> dict[str, list[WeightedBiome]]:
    tropical_wet = _w(("jungle", 0.55), ("bamboo_jungle", 0.25), ("sparse_jungle", 0.2))
    tropical_monsoon = _w(("jungle", 0.45), ("sparse_jungle", 0.3), ("mangrove_swamp", 0.25))
    tropical_savanna = _w(("savanna", 0.55), ("sparse_jungle", 0.2), ("savanna_plateau", 0.25))
    hot_desert = _w(("desert", 1.0))
    cold_desert = _w(("desert", 0.55), ("badlands", 0.3), ("eroded_badlands", 0.15))
    hot_steppe = _w(("savanna", 0.5), ("savanna_plateau", 0.3), ("desert", 0.2))
    cold_steppe = _w(("plains", 0.4), ("windswept_savanna", 0.2), ("badlands", 0.2), ("savanna", 0.2))
    med_hot = _w(("plains", 0.4), ("forest", 0.3), ("savanna", 0.3))
    med_warm = _w(("forest", 0.4), ("plains", 0.3), ("flower_forest", 0.3))
    med_cold = _w(("taiga", 0.5), ("meadow", 0.5))
    monsoon_hot = _w(("forest", 0.4), ("plains", 0.3), ("sparse_jungle", 0.3))
    monsoon_warm = _w(("forest", 0.4), ("meadow", 0.3), ("plains", 0.3))
    monsoon_cold = _w(("taiga", 0.6), ("meadow", 0.4))
    humid_subtropical = _w(("forest", 0.35), ("plains", 0.3), ("cherry_grove", 0.15),
                           ("flower_forest", 0.1), ("swamp", 0.1))
    oceanic = _w(("forest", 0.35), ("plains", 0.25), ("birch_forest", 0.2),
                 ("flower_forest", 0.1), ("meadow", 0.1))
    subpolar_oceanic = _w(("taiga", 0.5), ("meadow", 0.3), ("old_growth_pine_taiga", 0.2))
    continental_hot = _w(("forest", 0.35), ("plains", 0.3), ("birch_forest", 0.2), ("dark_forest", 0.15))
    continental_warm = _w(("taiga", 0.3), ("forest", 0.3), ("birch_forest", 0.2),
                          ("old_growth_birch_forest", 0.2))
    subarctic = _w(("taiga", 0.5), ("old_growth_spruce_taiga", 0.3), ("snowy_taiga", 0.2))
    subarctic_severe = _w(("snowy_taiga", 0.6), ("snowy_plains", 0.4))
    tundra = _w(("snowy_plains", 0.55), ("grove", 0.3), ("snowy_slopes", 0.1), ("ice_spikes", 0.05))
    ice_cap = _w(("frozen_peaks", 0.45), ("snowy_plains", 0.45), ("ice_spikes", 0.1))
    return {
        "Af": tropical_wet, "Am": tropical_monsoon, "Aw": tropical_savanna,
        "BWh": hot_desert, "BWk": cold_desert, "BSh": hot_steppe, "BSk": cold_steppe,
        "Csa": med_hot, "Csb": med_warm, "Csc": med_cold,
        "Cwa": monsoon_hot, "Cwb": monsoon_warm, "Cwc": monsoon_cold,
        "Cfa": humid_subtropical, "Cfb": oceanic, "Cfc": subpolar_oceanic,
        "Dsa": continental_hot, "Dsb": continental_warm, "Dsc": subarctic, "Dsd": subarctic_severe,
        "Dwa": continental_hot, "Dwb": continental_warm, "Dwc": subarctic, "Dwd": subarctic_severe,
        "Dfa": continental_hot, "Dfb": continental_warm, "Dfc": subarctic, "Dfd": subarctic_severe,
        "ET": tundra, "EF": ice_cap,
    }


def default_elevation_bands() -> list[ElevationBand]:
    def b(m, biome, *climates):
        return ElevationBand(min_m=m, biome=f"minecraft:{biome}", climates=list(climates))
    return [
        b(5000, "frozen_peaks"),
        # cold groups: snow line is low
        b(2500, "jagged_peaks", "D", "E"), b(1900, "snowy_slopes", "D", "E"), b(1300, "grove", "D", "E"),
        # temperate
        b(3400, "jagged_peaks", "C"), b(2800, "snowy_slopes", "C"), b(2200, "grove", "C"),
        b(1600, "windswept_forest", "C"),
        # tropical and arid: peaks stay bare far higher
        b(4300, "jagged_peaks", "A", "B"), b(3600, "snowy_slopes", "A", "B"),
        b(2800, "stony_peaks", "A", "B"), b(2000, "windswept_hills", "A", "B"),
    ]


def default_biome_settings() -> BiomeSettings:
    return BiomeSettings(rules=default_rules(), elevation_bands=default_elevation_bands())


def default_structure_entries() -> list[StructureEntry]:
    from .vanilla_structures import DEFAULT_ENTRIES
    return [e.model_copy(deep=True) for e in DEFAULT_ENTRIES]

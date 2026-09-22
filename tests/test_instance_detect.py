import json
from pathlib import Path

import pytest

from minemap.instance import detect


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")


def make_prism(root: Path, name="Pack", mc="1.21.1", loader_uid="net.neoforged.neoforge", loader_ver="21.1.234"):
    inst = root / "instances" / name
    comps = [{"uid": "net.minecraft", "version": mc}]
    if loader_uid:
        comps.append({"uid": loader_uid, "version": loader_ver})
    _write(inst / "mmc-pack.json", {"components": comps, "formatVersion": 1})
    _write(inst / "instance.cfg", f"InstanceType=OneSix\nname={name} Display\n")
    (inst / ".minecraft" / "mods").mkdir(parents=True)
    jar = root / "libraries" / "com" / "mojang" / "minecraft" / mc / f"minecraft-{mc}-client.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"PK")
    return inst


def test_mmc_instances_read_loader_version_and_jar(tmp_path):
    make_prism(tmp_path)
    make_prism(tmp_path, name="Fab", mc="1.20.1", loader_uid="net.fabricmc.fabric-loader", loader_ver="0.15")
    found = detect._mmc_instances(tmp_path, "prism")
    assert [i.name for i in found] == ["Fab Display", "Pack Display"]
    pack = next(i for i in found if i.name == "Pack Display")
    assert pack.launcher == "prism"
    assert pack.loader == "neoforge"
    assert pack.mc_version == "1.21.1"
    assert pack.game_dir.endswith(".minecraft")
    assert Path(pack.vanilla_jar).name == "minecraft-1.21.1-client.jar"
    fab = next(i for i in found if i.name == "Fab Display")
    assert fab.loader == "fabric" and fab.mc_version == "1.20.1"


def test_mmc_malformed_pack_is_skipped_not_raised(tmp_path):
    inst = tmp_path / "instances" / "Broken"
    _write(inst / "mmc-pack.json", "{not json")
    (inst / ".minecraft").mkdir()
    make_prism(tmp_path, name="Good")
    found = detect._mmc_instances(tmp_path, "polymc")
    assert [i.name for i in found] == ["Good Display"]


def test_mmc_missing_minecraft_component_warns(tmp_path):
    inst = tmp_path / "instances" / "NoMc"
    _write(inst / "mmc-pack.json", {"components": [{"uid": "net.minecraftforge", "version": "47.2"}]})
    (inst / ".minecraft").mkdir()
    found = detect._mmc_instances(tmp_path, "multimc")
    assert found[0].loader == "forge"
    assert found[0].mc_version == ""
    assert found[0].warnings


def test_curseforge_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    inst = tmp_path / "curseforge" / "minecraft" / "Instances" / "MyPack"
    _write(inst / "minecraftinstance.json", {
        "name": "My Pack", "gameVersion": "1.21.1",
        "baseModLoader": {"name": "neoforge-21.1.234", "minecraftVersion": "1.21.1"}})
    (inst / "mods").mkdir()
    jar = tmp_path / "curseforge" / "minecraft" / "Install" / "versions" / "1.21.1" / "1.21.1.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"PK")
    found = detect._curseforge_instances()
    assert len(found) == 1
    i = found[0]
    assert (i.launcher, i.name, i.loader, i.mc_version) == ("curseforge", "My Pack", "neoforge", "1.21.1")
    assert i.game_dir == str(inst)
    assert i.vanilla_jar == str(jar)


def test_modrinth_profile_json(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_appdata", lambda: tmp_path)
    root = tmp_path / "com.modrinth.theseus"
    prof = root / "profiles" / "cobble"
    _write(prof / "profile.json", {"metadata": {"name": "Cobble", "loader": "fabric", "game_version": "1.20.4"}})
    (prof / "mods").mkdir()
    found = detect._modrinth_instances()
    assert len(found) == 1
    assert (found[0].name, found[0].loader, found[0].mc_version) == ("Cobble", "fabric", "1.20.4")


def test_modrinth_sqlite_profiles(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(detect, "_appdata", lambda: tmp_path)
    root = tmp_path / "ModrinthApp"
    (root / "profiles" / "p1" / "mods").mkdir(parents=True)
    con = sqlite3.connect(root / "app.db")
    con.execute("CREATE TABLE profiles (path TEXT, name TEXT, game_version TEXT, mod_loader TEXT)")
    con.execute("INSERT INTO profiles VALUES ('p1', 'From DB', '1.21.1', 'neoforge')")
    con.commit()
    con.close()
    found = detect._modrinth_instances()
    assert found[0].name == "From DB"
    assert found[0].loader == "neoforge"
    assert found[0].mc_version == "1.21.1"


def test_vanilla_versions_dir_loader_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_appdata", lambda: tmp_path)
    game = tmp_path / ".minecraft"
    _write(game / "versions" / "1.21.1" / "1.21.1.json", {"id": "1.21.1"})
    _write(game / "versions" / "neoforge-21.1.234" / "neoforge-21.1.234.json", {"id": "neoforge-21.1.234", "inheritsFrom": "1.21.1"})
    (game / "versions" / "1.21.1" / "1.21.1.jar").write_bytes(b"PK")
    (game / "mods").mkdir()
    found = detect._vanilla_instances()
    assert len(found) == 1
    assert found[0].loader == "neoforge"
    assert found[0].mc_version == "1.21.1"
    assert found[0].vanilla_jar.endswith("1.21.1.jar")


def test_describe_folder_accepts_mods_dir_and_instance_root(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_mmc_roots", lambda: [])
    inst = tmp_path / "SomePack"
    (inst / ".minecraft" / "mods").mkdir(parents=True)
    a = detect.describe_folder(str(inst))
    b = detect.describe_folder(str(inst / ".minecraft" / "mods"))
    assert a.game_dir == b.game_dir == str(inst / ".minecraft")
    assert a.launcher == "folder"
    assert a.name == "SomePack"


def test_describe_folder_rejects_random_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_mmc_roots", lambda: [])
    with pytest.raises(ValueError):
        detect.describe_folder(str(tmp_path))


def test_detect_instances_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_mmc_roots", lambda: [(tmp_path / "nowhere", "prism")])
    monkeypatch.setattr(detect, "_curseforge_instances", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(detect, "_modrinth_instances", lambda: [])
    monkeypatch.setattr(detect, "_vanilla_instances", lambda: [])
    assert detect.detect_instances() == []

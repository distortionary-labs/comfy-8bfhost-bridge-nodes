import json
import os

from comfy_8bfhost_bridge import config as config_module
from comfy_8bfhost_bridge.catalog import NO_FILTERS, NO_HOST, Catalog, Filter


def test_config_file(tmp_path):
    path = tmp_path / "config.json"
    assert config_module.load(str(path)) == config_module.Config()
    path.write_text(json.dumps({"host_path": "/opt/8bf-host", "folders": ["/a", "/b c"]}))
    assert config_module.load(str(path)) == config_module.Config("/opt/8bf-host", ["/a", "/b c"])


def test_config_ignores_garbage(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text("not json")
    assert config_module.load(str(path)) == config_module.Config()
    path.write_text(json.dumps({"host_path": 3, "folders": "/a"}))
    assert config_module.load(str(path)) == config_module.Config()
    path.write_text(json.dumps({"host_path": " ~/bin/8bf-host ", "folders": [" ", 3, "/a"]}))
    loaded = config_module.load(str(path))
    assert loaded.host_path == os.path.expanduser("~/bin/8bf-host")
    assert loaded.folders == ["/a"]
    path.write_text("[]")
    assert config_module.load(str(path)) == config_module.Config()
    assert "ignoring" in capsys.readouterr().err


def test_preset_store(tmp_path):
    store = config_module.PresetStore(str(tmp_path / "presets"))
    assert store.existing("1a2b3c4d5e6f") is None
    # Looking a preset up does not create the folder; preparing to save one does.
    assert not (tmp_path / "presets").exists()
    path = store.save_path("1a2b3c4d5e6f")
    assert path == store.path("1a2b3c4d5e6f")
    assert os.path.dirname(path) == str(tmp_path / "presets")
    open(path, "w").write("{}")
    assert store.existing("1a2b3c4d5e6f") == path
    # Names come from users and 8bf-host; never let one name a path outside the folder.
    assert os.path.dirname(store.path("../x")) == str(tmp_path / "presets")
    assert os.path.dirname(store.path("Acme/Unsharp Mask")) == str(tmp_path / "presets")
    # Names that differ only in characters a file name cannot hold keep separate presets.
    assert store.path("Acme/Sharpen") != store.path("Acme_Sharpen")
    assert store.path("Acme/Sharpen") != store.path("Acme:Sharpen")
    assert len(os.path.basename(store.path("x" * 1000))) < 120
    assert config_module.preset_name("") != ".json"


def test_digest(tmp_path):
    path = tmp_path / "preset.json"
    assert config_module.digest(str(path)) == ""
    path.write_text("{}")
    first = config_module.digest(str(path))
    assert first and first == config_module.digest(str(path))
    path.write_text("{ }")
    assert config_module.digest(str(path)) != first


def summary(**values):
    payload = {
        "id": "8c8224326f39",
        "name": "Invert",
        "category": "Wine",
        "path": "/f/invert.8bf",
        "format": "pe",
        "archs": ["win-x64"],
        "modes": ["rgb8", "gray8"],
        "has_about_box": True,
        "runs": "under Wine (win-x64)",
        "problem": None,
    }
    payload.update(values)
    return payload


def test_filter_from_list_json():
    entry = Filter.from_json(summary())
    assert entry.id == "8c8224326f39"
    assert entry.label == "Wine/Invert"
    assert entry.runnable
    blocked = Filter.from_json(summary(runs=None, problem="needs Wine"))
    assert not blocked.runnable
    assert blocked.problem == "needs Wine"
    assert Filter.from_json(summary(category="")).category == "Other"
    odd = Filter.from_json(summary(archs="x64", modes=None, runs=1))
    assert (odd.archs, odd.modes, odd.runs) == ([], [], "1")


def test_catalog_choices():
    catalog = Catalog()
    assert catalog.choices() == {}
    assert catalog.placeholder() == NO_FILTERS
    catalog.load(
        [
            summary(id="b", name="zeta", category="Two"),
            summary(id="a", name="Alpha", category="one"),
            summary(id="c", name="beta", category="Two"),
            summary(id="d", name="Beta", category="two"),
            summary(id="e", name="Blocked", runs=None, problem="needs Wine"),
            {"name": "no id"},
            "not an object",
        ],
        [{"path": "/f/broken.8bf", "message": "no PiPL"}, None],
    )
    assert catalog.choices() == {
        "one/Alpha": "a",
        "Two/beta [c]": "c",
        "two/Beta [d]": "d",
        "Two/zeta": "b",
    }
    assert catalog.find("e").problem == "needs Wine"
    assert catalog.find("missing") is None
    assert catalog.problems == [("/f/broken.8bf", "no PiPL")]

    catalog.fail("8bf-host was not found")
    assert catalog.choices() == {}
    assert catalog.error == "8bf-host was not found"
    assert catalog.placeholder() == NO_HOST

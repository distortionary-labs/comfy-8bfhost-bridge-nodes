import importlib.util
import json
import math
import os
import sys

import pytest

torch = pytest.importorskip("torch")

from comfy_8bfhost_bridge import bridge, nodes  # noqa: E402
from comfy_8bfhost_bridge.catalog import NO_HOST  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(nodes, "user_dir", lambda: str(tmp_path / "user"))
    monkeypatch.setattr(nodes, "_catalog", nodes.Catalog())
    return tmp_path / "user"


def configure(user_dir, **values):
    user_dir.mkdir(exist_ok=True)
    (user_dir / "config.json").write_text(json.dumps(values))


def test_comfyui_entry_point():
    spec = importlib.util.spec_from_file_location(
        "comfy_8bfhost_bridge_nodes",
        os.path.join(ROOT, "__init__.py"),
        submodule_search_locations=[ROOT],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    assert set(module.NODE_CLASS_MAPPINGS) == {"EightBfFilter", "EightBfFilterByName"}
    assert set(module.NODE_DISPLAY_NAME_MAPPINGS) == set(module.NODE_CLASS_MAPPINGS)


def test_missing_host_shows_a_placeholder(user_dir, tmp_path):
    configure(user_dir, host_path=str(tmp_path / "missing-8bf-host"))
    inputs = nodes.EightBfFilter.INPUT_TYPES()
    assert inputs["required"]["filter"][0] == [NO_HOST]
    with pytest.raises(bridge.BridgeError, match="does not exist"):
        nodes.EightBfFilter().apply(torch.zeros(1, 1, 1, 3), NO_HOST, bridge.DEFAULTS, "8-bit")


def test_is_changed(user_dir, tmp_path):
    assert math.isnan(nodes.EightBfFilterByName.IS_CHANGED("x", bridge.SHOW_DIALOG))
    assert nodes.EightBfFilterByName.IS_CHANGED("x", bridge.DEFAULTS) == ""
    assert nodes.EightBfFilterByName.IS_CHANGED("x", bridge.LAST_USED) == ""
    open(nodes.presets().path("x"), "w").write("{}")
    saved = nodes.EightBfFilterByName.IS_CHANGED(" x ", bridge.LAST_USED)
    assert saved != ""
    preset = tmp_path / "p.json"
    preset.write_text("{}")
    assert nodes.EightBfFilterByName.IS_CHANGED("x", bridge.PRESET_FILE, str(preset)) == saved


def test_node_inputs_are_checked(user_dir):
    node = nodes.EightBfFilterByName()
    image = torch.zeros(1, 2, 2, 3)
    for args, options, message in (
        ((image, "x", bridge.DEFAULTS, "12-bit"), {}, "depth must be"),
        ((torch.zeros(2, 2, 3), "x", bridge.DEFAULTS, "8-bit"), {}, "IMAGE batch"),
        ((torch.zeros(0, 2, 2, 3), "x", bridge.DEFAULTS, "8-bit"), {}, "batch is empty"),
        ((image, "x", bridge.DEFAULTS, "8-bit"), {"mask": torch.zeros(1, 1, 2, 2)}, "MASK"),
    ):
        with pytest.raises(bridge.BridgeError, match=message):
            node.apply(*args, **options)


def test_by_name_needs_a_name(user_dir):
    with pytest.raises(bridge.BridgeError, match="Name the filter"):
        nodes.EightBfFilterByName().apply(torch.zeros(1, 1, 1, 3), " ", bridge.DEFAULTS, "8-bit")


def test_nodes_with_8bf_host(user_dir, eightbf_host, filter_folder):
    configure(user_dir, host_path=eightbf_host, folders=[filter_folder])
    inputs = nodes.EightBfFilter.INPUT_TYPES()
    assert inputs["required"]["filter"][0] == ["Tests/Error", "Tests/Invert"]

    image = torch.rand(2, 4, 5, 3)
    mask = torch.zeros(4, 5)
    mask[1:3, 1:4] = 1.0
    (out,) = nodes.EightBfFilter().apply(
        image, "Tests/Invert", bridge.LAST_USED, "8-bit", mask=mask
    )
    assert out.shape == image.shape and out.dtype == torch.float32
    expected = torch.where(mask[None, :, :, None] > 0, 1.0 - image, image)
    assert torch.allclose(out, expected, atol=1 / 255)

    (named,) = nodes.EightBfFilterByName().apply(image, "Tests/Invert", bridge.DEFAULTS, "16-bit")
    assert torch.allclose(named, 1.0 - image, atol=1 / 255)

    with pytest.raises(bridge.BridgeError, match="not installed"):
        nodes.EightBfFilter().apply(image, "Tests/Gone", bridge.DEFAULTS, "8-bit")

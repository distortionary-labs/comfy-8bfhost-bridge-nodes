"""The bridge's commands and image files against a real 8bf-host.

Opt-in: set EIGHTBF_HOST to a dynamically linked build (``cargo build``).
"""

import json

import numpy as np

from comfy_8bfhost_bridge import bridge, host
from comfy_8bfhost_bridge.catalog import Catalog
from comfy_8bfhost_bridge.config import PresetStore


def _catalog(eightbf_host, folder):
    catalog = Catalog()
    catalog.load(*host.list_filters(eightbf_host, [folder], rescan=True))
    return catalog


def _job(eightbf_host, folder, tmp_path, name, **values):
    filter_id = _catalog(eightbf_host, folder).choices()["Tests/" + name]
    return bridge.Job(
        executable=eightbf_host,
        folders=[folder],
        filter_id=filter_id,
        presets=PresetStore(str(tmp_path / "presets")),
        **values,
    )


def test_filters_are_listed(eightbf_host, filter_folder):
    choices = _catalog(eightbf_host, filter_folder).choices()
    assert sorted(choices) == ["Tests/Error", "Tests/Invert"]


def test_8bit_batch_with_preset(eightbf_host, filter_folder, tmp_path):
    job = _job(eightbf_host, filter_folder, tmp_path, "Invert")
    frame = np.arange(18, dtype=np.float32).reshape(2, 3, 3) / 255
    out = bridge.filter_frames(job, [frame, frame])
    assert out[0].shape == (2, 3, 3)
    assert np.allclose(out[0], 1.0 - frame, atol=1e-6)
    assert np.allclose(out[1], out[0])
    preset = job.presets.existing(job.filter_id)
    assert json.load(open(preset))["format"] == "8bf-host-preset"


def test_16bit_frame_with_mask(eightbf_host, filter_folder, tmp_path):
    job = _job(eightbf_host, filter_folder, tmp_path, "Invert", sample_bytes=2)
    frame = np.array([[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]], dtype=np.float32)
    mask = np.array([[1.0, 0.0]], dtype=np.float32)
    out = bridge.filter_frames(job, [frame], [mask])[0]
    # The selected pixel is inverted (through the 8-bit fallback).
    assert np.allclose(out[0, 0], 1.0 - frame[0, 0], atol=300 / 65535)
    # The unselected pixel keeps its 16-bit value.
    assert np.allclose(out[0, 1], frame[0, 1], atol=1 / 65535)


def test_filter_errors_come_back_as_messages(eightbf_host, filter_folder, tmp_path):
    job = _job(eightbf_host, filter_folder, tmp_path, "Error")
    try:
        bridge.filter_frames(job, [np.zeros((1, 1, 3), dtype=np.float32)])
    except bridge.BridgeError as exc:
        assert "failed on purpose" in str(exc)
    else:
        raise AssertionError("the Error filter succeeded")

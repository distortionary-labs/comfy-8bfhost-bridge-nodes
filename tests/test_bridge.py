import json

import numpy as np
import pytest

from comfy_8bfhost_bridge import bridge, host, images
from comfy_8bfhost_bridge.config import PresetStore


class FakeHost:
    """Stands in for ``host.run``: inverts the input and saves a preset."""

    def __init__(self, status="ok", message=""):
        self.commands = []
        self.status = status
        self.message = message

    def __call__(self, command, cancelled=lambda: False, cwd=None):
        self.commands.append(command)
        if self.status != "ok":
            return host.Result(self.status, self.message, log=["[host] details"])
        width, height, channels, sample_bytes, data = images.read_pam(command[-2])
        maxval = 255 if sample_bytes == 1 else 65535
        frame = images.from_samples(data, width, height, channels, sample_bytes)
        inverted = images.to_samples(1.0 - frame, sample_bytes)
        images.write_pam(command[-1], width, height, channels, sample_bytes, inverted)
        path = self.find(command, "--save-preset")
        if path:
            json.dump({"format": "8bf-host-preset", "maxval": maxval}, open(path, "w"))
        return host.Result("ok")

    @staticmethod
    def find(command, name):
        for argument in command[: command.index("--")]:
            if argument.startswith(name + "="):
                return argument[len(name) + 1 :]
        return None

    def option(self, index, name):
        return self.find(self.commands[index], name)


def job(tmp_path, **values):
    options = dict(
        executable="8bf-host",
        folders=["/filters"],
        filter_id="1a2b3c4d5e6f",
        presets=PresetStore(str(tmp_path / "presets")),
    )
    options.update(values)
    return bridge.Job(**options)


def frames(count=1, height=2, width=3):
    return [np.full((height, width, 3), 0.25, dtype=np.float32) for _ in range(count)]


def test_frames_are_filtered(tmp_path):
    fake = FakeHost()
    out = bridge.filter_frames(job(tmp_path), frames(2), runner=fake)
    assert len(out) == 2
    assert np.allclose(out[0], 0.75, atol=1 / 255)
    assert out[0].shape == (2, 3, 3) and out[0].dtype == np.float32
    command = fake.commands[0]
    assert command[:2] == ["8bf-host", "run"]
    assert command[-3] == "1a2b3c4d5e6f"
    assert fake.option(0, "--plugin-dir") == "/filters"
    assert fake.option(0, "--save-preset").endswith("1a2b3c4d5e6f.json")
    assert "--mask" not in command and "--dialog" not in command


def test_16bit_frames(tmp_path):
    fake = FakeHost()
    out = bridge.filter_frames(job(tmp_path, sample_bytes=2), frames(), runner=fake)
    assert np.allclose(out[0], 0.75, atol=1e-4)


def test_last_used_without_a_preset_runs_the_defaults(tmp_path):
    fake = FakeHost()
    bridge.filter_frames(job(tmp_path), frames(2), runner=fake)
    assert fake.option(0, "--preset") is None
    # The first run saved its settings, which the second replays.
    assert fake.option(1, "--preset") == fake.option(0, "--save-preset")
    # A later queue starts from them too.
    bridge.filter_frames(job(tmp_path), frames(), runner=fake)
    assert fake.option(2, "--preset") == fake.option(0, "--save-preset")


def test_dialog_shows_once_per_batch(tmp_path):
    fake = FakeHost()
    bridge.filter_frames(job(tmp_path, settings=bridge.SHOW_DIALOG), frames(3), runner=fake)
    assert ["--dialog" in command for command in fake.commands] == [True, False, False]
    assert fake.option(0, "--preset") is None
    assert fake.option(1, "--preset") == fake.option(0, "--save-preset")


def test_defaults_never_load_a_preset(tmp_path):
    fake = FakeHost()
    store = PresetStore(str(tmp_path / "presets"))
    open(store.save_path("1a2b3c4d5e6f"), "w").write("{}")
    bridge.filter_frames(job(tmp_path, settings=bridge.DEFAULTS), frames(2), runner=fake)
    assert [fake.option(i, "--preset") for i in range(2)] == [None, None]


def test_preset_file(tmp_path):
    fake = FakeHost()
    preset = tmp_path / "unsharp.json"
    preset.write_text("{}")
    settings = job(tmp_path, settings=bridge.PRESET_FILE, preset_file=" %s " % preset)
    bridge.filter_frames(settings, frames(2), runner=fake)
    assert [fake.option(i, "--preset") for i in range(2)] == [str(preset)] * 2
    # The user's file is left alone.
    assert preset.read_text() == "{}"

    with pytest.raises(bridge.BridgeError, match="no preset file"):
        bridge.filter_frames(job(tmp_path, settings=bridge.PRESET_FILE), frames(), runner=fake)
    missing = job(tmp_path, settings=bridge.PRESET_FILE, preset_file=str(tmp_path / "x.json"))
    with pytest.raises(bridge.BridgeError, match="does not exist"):
        bridge.filter_frames(missing, frames(), runner=fake)


def test_mask_and_its_bounds_are_passed(tmp_path):
    fake = FakeHost()
    mask = np.zeros((2, 3), dtype=np.float32)
    mask[1, 1:] = 1.0
    bridge.filter_frames(job(tmp_path), frames(), [mask], runner=fake)
    assert fake.option(0, "--rect") == "1,1,2,1"
    assert fake.option(0, "--mask").endswith("mask.pgm")


def test_masks_repeat_their_last_one(tmp_path):
    fake = FakeHost()
    empty = np.zeros((2, 3), dtype=np.float32)
    full = np.ones((2, 3), dtype=np.float32)
    out = bridge.filter_frames(job(tmp_path), frames(3), [empty, full], runner=fake)
    # The empty mask selects nothing, so the first frame never reaches 8bf-host.
    assert len(fake.commands) == 2
    assert np.allclose(out[0], 0.25)
    assert np.allclose(out[2], 0.75, atol=1 / 255)


def test_dialog_waits_for_a_frame_that_runs(tmp_path):
    fake = FakeHost()
    masks = [np.zeros((2, 3), dtype=np.float32), np.ones((2, 3), dtype=np.float32)]
    bridge.filter_frames(job(tmp_path, settings=bridge.SHOW_DIALOG), frames(2), masks, runner=fake)
    assert "--dialog" in fake.commands[0]


def test_mask_size_must_match(tmp_path):
    with pytest.raises(bridge.BridgeError, match="mask is 4x4 but the image is 3x2"):
        bridge.filter_frames(job(tmp_path), frames(), [np.ones((4, 4))], runner=FakeHost())


def test_failures(tmp_path):
    with pytest.raises(bridge.BridgeError, match="failed on purpose"):
        bridge.filter_frames(
            job(tmp_path), frames(), runner=FakeHost("error", "the filter failed on purpose")
        )
    with pytest.raises(bridge.Cancelled):
        bridge.filter_frames(job(tmp_path), frames(), runner=FakeHost("cancelled"))


def test_progress_is_reported_per_frame(tmp_path):
    ticks = []
    bridge.filter_frames(
        job(tmp_path), frames(3), runner=FakeHost(), progress=lambda: ticks.append(1)
    )
    assert len(ticks) == 3


def test_bad_frames_and_masks(tmp_path):
    fake = FakeHost()
    with pytest.raises(bridge.BridgeError, match="shape \\(height, width, channels\\)"):
        bridge.filter_frames(job(tmp_path), [np.zeros((2, 3))], runner=fake)
    with pytest.raises(bridge.BridgeError, match="empty"):
        bridge.filter_frames(job(tmp_path), [np.zeros((0, 3, 3))], runner=fake)
    with pytest.raises(bridge.BridgeError, match="5 channels"):
        bridge.filter_frames(job(tmp_path), [np.zeros((1, 1, 5))], runner=fake)
    with pytest.raises(bridge.BridgeError, match="masks of shape"):
        bridge.filter_frames(job(tmp_path), frames(), [np.ones((2, 3, 1))], runner=fake)
    assert fake.commands == []


def test_a_bad_frame_late_in_the_batch_fails_before_any_run(tmp_path):
    fake = FakeHost()
    batch = frames(2) + [np.zeros((2, 3, 5))]
    with pytest.raises(bridge.BridgeError, match="5 channels"):
        bridge.filter_frames(job(tmp_path, settings=bridge.SHOW_DIALOG), batch, runner=fake)
    masks = [np.ones((2, 3)), np.ones((4, 4))]
    with pytest.raises(bridge.BridgeError, match="mask is 4x4"):
        bridge.filter_frames(job(tmp_path), frames(2), masks, runner=fake)
    assert fake.commands == []


def test_bad_job_settings(tmp_path):
    fake = FakeHost()
    for values, message in (
        ({"filter_id": ""}, "No filter"),
        ({"sample_bytes": 3}, "1 or 2 bytes"),
        ({"resolution": float("nan")}, "positive number"),
        ({"resolution": 0.0}, "positive number"),
        ({"settings": "sometimes"}, "Unknown settings"),
    ):
        with pytest.raises(bridge.BridgeError, match=message):
            bridge.filter_frames(job(tmp_path, **values), frames(), runner=fake)
    assert fake.commands == []


def test_nan_pixels_do_not_reach_the_filter_as_garbage(tmp_path):
    frame = np.full((1, 2, 3), np.nan, dtype=np.float32)
    frame[0, 1] = np.inf
    out = bridge.filter_frames(job(tmp_path), [frame], runner=FakeHost())[0]
    # NaN went in as 0 and +inf as 1, so the inverted result is 1 and 0.
    assert np.allclose(out[0, 0], 1.0) and np.allclose(out[0, 1], 0.0)


def test_output_of_another_size_is_refused(tmp_path):
    def shrinking(command, cancelled=lambda: False, cwd=None):
        images.write_pam(command[-1], 1, 1, 3, 1, bytes(3))
        return host.Result("ok")

    with pytest.raises(bridge.BridgeError, match="1x1 image with 3 channels for a 3x2"):
        bridge.filter_frames(job(tmp_path), frames(), runner=shrinking)


def test_missing_output_is_reported(tmp_path):
    runner = lambda command, cancelled=lambda: False, cwd=None: host.Result("ok")
    with pytest.raises(bridge.BridgeError, match="Could not read the filtered image"):
        bridge.filter_frames(job(tmp_path), frames(), runner=runner)


def test_colors():
    assert bridge.parse_color("#ff8000") == (255, 128, 0)
    assert bridge.parse_color(" 00ff00 ") == (0, 255, 0)
    for text in ("red", "#fff", "#gg0000"):
        with pytest.raises(bridge.BridgeError, match="not a color"):
            bridge.parse_color(text)

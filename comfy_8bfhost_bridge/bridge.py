"""Running a filter over a batch of frames through 8bf-host.

Frames and masks are numpy arrays here; the nodes convert ComfyUI's
tensors. Each frame is one ``8bf-host run`` in its own temporary folder.
"""

import math
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from . import host, images
from .config import PresetStore

INPUT_NAME = "frame.pam"
OUTPUT_NAME = "filtered.pam"
MASK_NAME = "mask.pgm"
# Set to log every 8bf-host command line and its output.
DEBUG_VARIABLE = "COMFY_8BFHOST_BRIDGE_DEBUG"

# Where a run's filter settings come from.
LAST_USED = "last used"
SHOW_DIALOG = "show dialog"
DEFAULTS = "defaults"
PRESET_FILE = "preset file"
SETTINGS_MODES = [LAST_USED, SHOW_DIALOG, DEFAULTS, PRESET_FILE]


class BridgeError(RuntimeError):
    """The frames cannot be filtered, or 8bf-host failed on one."""


class Cancelled(Exception):
    """8bf-host stopped because the run was cancelled."""


@dataclass
class Job:
    executable: str
    folders: List[str]
    filter_id: str
    presets: PresetStore
    settings: str = LAST_USED
    preset_file: str = ""
    sample_bytes: int = 1
    foreground: Tuple[int, int, int] = (0, 0, 0)
    background: Tuple[int, int, int] = (255, 255, 255)
    resolution: float = 72.0
    allow_8bit_fallback: bool = True


def parse_color(text):
    """Parse ``#rrggbb`` (the ``#`` is optional) into an (r, g, b) tuple."""
    value = text.strip().lstrip("#")
    if len(value) != 6:
        raise BridgeError("%r is not a color; write it as #rrggbb." % text)
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise BridgeError("%r is not a color; write it as #rrggbb." % text) from None


def settings_preset(job):
    """The preset the first frame starts from, and whether it shows the dialog.

    Later frames of a batch always replay what the first one saved, so a
    dialog appears once per batch.
    """
    if job.settings == SHOW_DIALOG:
        return None, True
    if job.settings == DEFAULTS:
        return None, False
    if job.settings == PRESET_FILE:
        path = os.path.expanduser(job.preset_file.strip())
        if not path:
            raise BridgeError('Settings is "preset file", but no preset file is given.')
        if not os.path.isfile(path):
            raise BridgeError("The preset file %s does not exist." % path)
        return path, False
    if job.settings == LAST_USED:
        return job.presets.existing(job.filter_id), False
    raise BridgeError("Unknown settings choice %r." % job.settings)


def frame_mask(masks, index, height, width):
    """The mask for frame ``index``. A shorter batch of masks repeats its last one."""
    if masks is None or len(masks) == 0:
        return None
    mask = masks[min(index, len(masks) - 1)]
    if np.ndim(mask) != 2:
        raise BridgeError("Expected masks of shape (height, width), got %r." % (np.shape(mask),))
    if mask.shape != (height, width):
        raise BridgeError(
            "The mask is %dx%d but the image is %dx%d."
            % (mask.shape[1], mask.shape[0], width, height)
        )
    return mask


def _log(lines):
    for line in lines:
        sys.stderr.write("comfy_8bfhost_bridge: %s\n" % line)


def _check_job(job):
    if not job.filter_id:
        raise BridgeError("No filter is chosen.")
    if job.sample_bytes not in images.SAMPLE_FORMATS:
        raise BridgeError("Samples are 1 or 2 bytes, not %r." % (job.sample_bytes,))
    if not (math.isfinite(job.resolution) and job.resolution > 0):
        raise BridgeError("The resolution must be a positive number, not %r." % job.resolution)


def _check_frame(frame):
    if np.ndim(frame) != 3:
        raise BridgeError(
            "Expected images of shape (height, width, channels), got %r." % (np.shape(frame),)
        )
    height, width, channels = frame.shape
    if channels not in images.TUPLE_TYPES:
        raise BridgeError("Images with %d channels cannot be filtered." % channels)
    if height <= 0 or width <= 0:
        raise BridgeError("The image is empty (%dx%d)." % (width, height))


def _write_inputs(directory, frame, mask, sample_bytes):
    """Write the frame, and the mask if there is one; return both paths."""
    height, width, channels = frame.shape
    input_path = os.path.join(directory, INPUT_NAME)
    mask_path = None if mask is None else os.path.join(directory, MASK_NAME)
    try:
        samples = images.to_samples(frame, sample_bytes)
        images.write_pam(input_path, width, height, channels, sample_bytes, samples)
        if mask_path is not None:
            images.write_pgm(mask_path, width, height, images.mask_to_gray(mask))
    except OSError as exc:
        raise BridgeError("Could not write the files 8bf-host works on: %s" % exc) from exc
    return input_path, mask_path


def _read_output(path, shape):
    """Read 8bf-host's result, which must have the input's ``shape``."""
    height, width, channels = shape
    try:
        out_width, out_height, out_channels, out_bytes, data = images.read_pam(path)
    except (OSError, ValueError) as exc:
        raise BridgeError("Could not read the filtered image: %s" % exc) from exc
    if (out_width, out_height, out_channels) != (width, height, channels):
        raise BridgeError(
            "8bf-host returned a %dx%d image with %d channels for a %dx%d one with %d."
            % (out_width, out_height, out_channels, width, height, channels)
        )
    return images.from_samples(data, width, height, channels, out_bytes)


class _Batch:
    """One job's runs over the frames of a batch.

    The first frame starts from ``settings_preset``. Once a frame has run,
    the rest replay the settings it saved, so a dialog appears once per batch.
    """

    def __init__(self, job, runner, cancelled):
        self.job = job
        self.runner = runner
        self.cancelled = cancelled
        self.preset, self.dialog = settings_preset(job)
        try:
            self.save_preset = job.presets.save_path(job.filter_id)
        except OSError as exc:
            raise BridgeError("Could not create the presets folder: %s" % exc) from exc

    def filter(self, frame, mask):
        rect = None
        if mask is not None:
            rect = images.mask_bounds(mask)
            if rect is None:
                # Nothing is selected, so the filter would change nothing.
                return np.array(frame, dtype=np.float32)
        directory = tempfile.mkdtemp(prefix="comfy_8bfhost_bridge-")
        try:
            filtered = self._run(directory, frame, mask, rect)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if self.dialog or self.job.settings == LAST_USED:
            self.preset = self.job.presets.existing(self.job.filter_id) or self.preset
            self.dialog = False
        return filtered

    def _run(self, directory, frame, mask, rect):
        job = self.job
        input_path, mask_path = _write_inputs(directory, frame, mask, job.sample_bytes)
        output_path = os.path.join(directory, OUTPUT_NAME)
        command = host.run_command(
            job.executable,
            job.folders,
            job.filter_id,
            input_path,
            output_path,
            dialog=self.dialog,
            preset=self.preset,
            save_preset=self.save_preset,
            mask=mask_path,
            rect=rect,
            foreground=job.foreground,
            background=job.background,
            resolution=job.resolution,
            allow_8bit_fallback=job.allow_8bit_fallback,
        )
        debug = bool(os.environ.get(DEBUG_VARIABLE))
        if debug:
            _log(["running " + " ".join(command)])
        result = self.runner(command, cancelled=self.cancelled, cwd=directory)
        if debug or result.status == host.ERROR:
            _log(result.log)
        if result.status == host.CANCELLED:
            raise Cancelled()
        if not result.ok:
            raise BridgeError(result.message or "The filter failed.")
        return _read_output(output_path, frame.shape)


def filter_frames(
    job, frames, masks=None, runner=host.run, cancelled=lambda: False, progress=lambda: None
):
    """Run ``job``'s filter on each frame and return the filtered frames."""
    _check_job(job)
    frames = list(frames)
    for frame in frames:
        _check_frame(frame)
    # Everything is checked before the first run, so a bad frame late in the
    # batch does not fail after the user has answered the filter's dialog.
    frame_masks = [frame_mask(masks, index, *frame.shape[:2]) for index, frame in enumerate(frames)]
    batch = _Batch(job, runner, cancelled)
    out = []
    for frame, mask in zip(frames, frame_masks):
        out.append(batch.filter(frame, mask))
        progress()
    return out

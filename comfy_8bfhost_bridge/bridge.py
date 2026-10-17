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

import numpy as np

from . import host, images

INPUT_NAME = "frame.pam"
OUTPUT_NAME = "filtered.pam"
MASK_NAME = "mask.pgm"

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
    folders: list
    filter_id: str
    presets: object
    settings: str = LAST_USED
    preset_file: str = ""
    sample_bytes: int = 1
    foreground: tuple = (0, 0, 0)
    background: tuple = (255, 255, 255)
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


def _filter_frame(job, frame, mask, preset, dialog, runner, cancelled, directory):
    """Filter one frame. Returns None when the mask selects nothing."""
    height, width, channels = frame.shape
    if channels not in images.TUPLE_TYPES:
        raise BridgeError("Images with %d channels cannot be filtered." % channels)
    if height <= 0 or width <= 0:
        raise BridgeError("The image is empty (%dx%d)." % (width, height))
    input_path = os.path.join(directory, INPUT_NAME)
    output_path = os.path.join(directory, OUTPUT_NAME)
    mask_path = None
    rect = None
    if mask is not None:
        rect = images.mask_bounds(mask)
        if rect is None:
            return None
    try:
        images.write_pam(
            input_path,
            width,
            height,
            channels,
            job.sample_bytes,
            images.to_samples(frame, job.sample_bytes),
        )
        if mask is not None:
            mask_path = os.path.join(directory, MASK_NAME)
            images.write_pgm(mask_path, width, height, images.mask_to_gray(mask))
        save_preset = job.presets.path(job.filter_id)
    except OSError as exc:
        raise BridgeError("Could not write the files 8bf-host works on: %s" % exc) from exc

    command = host.run_command(
        job.executable,
        job.folders,
        job.filter_id,
        input_path,
        output_path,
        dialog=dialog,
        preset=preset,
        save_preset=save_preset,
        mask=mask_path,
        rect=rect,
        foreground=job.foreground,
        background=job.background,
        resolution=job.resolution,
        allow_8bit_fallback=job.allow_8bit_fallback,
    )
    if os.environ.get("COMFY_8BFHOST_BRIDGE_DEBUG"):
        _log(["running " + " ".join(command)])
    result = runner(command, cancelled=cancelled, cwd=directory)
    if os.environ.get("COMFY_8BFHOST_BRIDGE_DEBUG") or result.status == "error":
        _log(result.log)
    if result.status == "cancelled":
        raise Cancelled()
    if not result.ok:
        raise BridgeError(result.message or "The filter failed.")
    try:
        out_width, out_height, out_channels, out_bytes, data = images.read_pam(output_path)
    except (OSError, ValueError) as exc:
        raise BridgeError("Could not read the filtered image: %s" % exc) from exc
    if (out_width, out_height, out_channels) != (width, height, channels):
        raise BridgeError(
            "8bf-host returned a %dx%d image with %d channels for a %dx%d one with %d."
            % (out_width, out_height, out_channels, width, height, channels)
        )
    return images.from_samples(data, width, height, channels, out_bytes)


def filter_frames(
    job, frames, masks=None, runner=host.run, cancelled=lambda: False, progress=lambda: None
):
    """Run ``job``'s filter on each frame and return the filtered frames."""
    if not job.filter_id:
        raise BridgeError("No filter is chosen.")
    if job.sample_bytes not in (1, 2):
        raise BridgeError("Samples are 1 or 2 bytes, not %r." % (job.sample_bytes,))
    if not (math.isfinite(job.resolution) and job.resolution > 0):
        raise BridgeError("The resolution must be a positive number, not %r." % job.resolution)
    preset, dialog = settings_preset(job)
    out = []
    for index, frame in enumerate(frames):
        if np.ndim(frame) != 3:
            raise BridgeError(
                "Expected images of shape (height, width, channels), got %r." % (np.shape(frame),)
            )
        height, width = frame.shape[:2]
        mask = frame_mask(masks, index, height, width)
        directory = tempfile.mkdtemp(prefix="comfy_8bfhost_bridge-")
        try:
            filtered = _filter_frame(job, frame, mask, preset, dialog, runner, cancelled, directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if filtered is None:
            # Nothing is selected, so the filter would change nothing.
            out.append(np.array(frame, dtype=np.float32))
        else:
            out.append(filtered)
        if filtered is not None and (dialog or job.settings == LAST_USED):
            # The first run saved the settings it used; the rest replay them.
            preset = job.presets.existing(job.filter_id) or preset
            dialog = False
        progress()
    return out

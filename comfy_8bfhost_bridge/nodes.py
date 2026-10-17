"""The ComfyUI nodes: definitions and the glue between the parts."""

import os
import sys

import numpy as np
import torch

from . import bridge, host
from . import config as config_module
from .catalog import Catalog

try:
    import folder_paths
except ImportError:  # Imported outside ComfyUI, e.g. by the tests.
    folder_paths = None

try:
    import comfy.utils as comfy_utils
    from comfy import model_management
except ImportError:
    comfy_utils = None
    model_management = None

CATEGORY = "image/filters/8bf"
DEPTHS = {"8-bit": 1, "16-bit": 2}
# Filters may also be dropped into ComfyUI's models folder, under this name.
MODELS_SUBFOLDER = "8bf"

_catalog = Catalog()


def user_dir():
    """Where the configuration file and presets live."""
    if folder_paths is not None:
        base = folder_paths.get_user_directory()
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, config_module.FOLDER_NAME)


def config_path():
    return os.path.join(user_dir(), config_module.CONFIG_NAME)


def presets():
    return config_module.PresetStore(os.path.join(user_dir(), "presets"))


def load_config():
    config = config_module.load(config_path())
    if folder_paths is not None:
        models = os.path.join(folder_paths.models_dir, MODELS_SUBFOLDER)
        if os.path.isdir(models):
            config.folders.append(models)
    return config


def scan(config=None):
    """Ask 8bf-host for its filters and keep the list for the nodes.

    ComfyUI asks for the node list from its web server's thread while a
    queue runs in another, so a new catalog is built and then swapped in
    rather than changing the one a run may be reading.
    """
    global _catalog
    config = config or load_config()
    catalog = Catalog()
    try:
        executable = host.require_executable(config.host_path, config_path())
        catalog.load(*host.list_filters(executable, config.folders))
    except host.HostError as exc:
        catalog.fail(str(exc))
        sys.stderr.write("comfy_8bfhost_bridge: listing filters failed: %s\n" % exc)
    _catalog = catalog
    return catalog


def resolve(label):
    """Turn a filter list entry back into the filter's id."""
    filter_id = _catalog.choices().get(label)
    if filter_id is not None:
        return filter_id
    catalog = scan()
    filter_id = catalog.choices().get(label)
    if filter_id is not None:
        return filter_id
    if catalog.error:
        raise bridge.BridgeError(catalog.error)
    raise bridge.BridgeError("The filter %s is not installed, or cannot run here." % label)


def _interrupted():
    return model_management is not None and model_management.processing_interrupted()


def run(
    filter_id,
    image,
    settings,
    depth,
    mask=None,
    preset_file="",
    foreground="#000000",
    background="#ffffff",
    resolution=72.0,
    allow_8bit_fallback=True,
    config=None,
):
    """Filter an IMAGE batch, one 8bf-host run per frame."""
    if depth not in DEPTHS:
        raise bridge.BridgeError("depth must be one of %s, not %r." % (", ".join(DEPTHS), depth))
    if not isinstance(image, torch.Tensor) or image.ndim != 4:
        raise bridge.BridgeError(
            "Expected an IMAGE batch of shape (batch, height, width, channels)."
        )
    if image.shape[0] == 0:
        raise bridge.BridgeError("The image batch is empty.")
    if mask is not None and (not isinstance(mask, torch.Tensor) or mask.ndim not in (2, 3)):
        raise bridge.BridgeError(
            "Expected a MASK of shape (height, width) or (batch, height, width)."
        )
    config = config or load_config()
    job = bridge.Job(
        executable=host.require_executable(config.host_path, config_path()),
        folders=config.folders,
        filter_id=filter_id,
        presets=presets(),
        settings=settings,
        preset_file=preset_file or "",
        sample_bytes=DEPTHS[depth],
        foreground=bridge.parse_color(foreground),
        background=bridge.parse_color(background),
        resolution=float(resolution),
        allow_8bit_fallback=bool(allow_8bit_fallback),
    )
    frames = list(image.detach().cpu().float().numpy())
    masks = None
    if mask is not None:
        masks = mask.detach().cpu().float().numpy()
        masks = list(masks[None] if masks.ndim == 2 else masks)
    bar = comfy_utils.ProgressBar(len(frames)) if comfy_utils is not None else None
    try:
        out = bridge.filter_frames(
            job,
            frames,
            masks,
            cancelled=_interrupted,
            progress=lambda: bar.update(1) if bar is not None else None,
        )
    except bridge.Cancelled:
        if model_management is not None:
            model_management.throw_exception_if_processing_interrupted()
        raise bridge.BridgeError("The filter was cancelled.") from None
    return torch.from_numpy(np.stack(out))


def changed(filter_id, settings, preset_file=""):
    """What ComfyUI compares to decide whether to run the node again."""
    if settings == bridge.SHOW_DIALOG:
        # Not equal to itself, so the dialog shows on every run.
        return float("nan")
    if settings == bridge.LAST_USED:
        return config_module.digest(presets().path(filter_id))
    if settings == bridge.PRESET_FILE:
        return config_module.digest(os.path.expanduser((preset_file or "").strip()))
    return ""


def _inputs(filter_input):
    return {
        "required": {
            "image": ("IMAGE",),
            "filter": filter_input,
            "settings": (
                bridge.SETTINGS_MODES,
                {
                    "default": bridge.LAST_USED,
                    "tooltip": "last used: the settings of this filter's last run, or its "
                    "defaults. show dialog: open the filter's dialog (needs a desktop on the "
                    "machine running ComfyUI). defaults: the filter's own defaults. preset "
                    "file: an 8bf-host preset file.",
                },
            ),
            "depth": (
                list(DEPTHS),
                {
                    "default": "8-bit",
                    "tooltip": "The sample depth handed to the filter. 8-bit-only filters "
                    "run on an 8-bit copy of a 16-bit image.",
                },
            ),
        },
        "optional": {
            "mask": ("MASK", {"tooltip": "Only filter where the mask is white."}),
            "preset_file": (
                "STRING",
                {"default": "", "tooltip": 'An 8bf-host preset, used with "preset file".'},
            ),
            "foreground": ("STRING", {"default": "#000000"}),
            "background": ("STRING", {"default": "#ffffff"}),
            "resolution": ("FLOAT", {"default": 72.0, "min": 1.0, "max": 10000.0, "step": 1.0}),
            "allow_8bit_fallback": ("BOOLEAN", {"default": True}),
        },
    }


class EightBfFilter:
    """Runs a filter chosen from the list 8bf-host reports."""

    CATEGORY = CATEGORY
    DESCRIPTION = "Run a Photoshop-compatible filter plug-in (.8bf) through 8bf-host."
    FUNCTION = "apply"
    RETURN_TYPES = ("IMAGE",)

    @classmethod
    def INPUT_TYPES(cls):
        # ComfyUI calls this whenever the browser reloads the node list, so a
        # refresh picks up new filters. 8bf-host caches its scan.
        labels = list(scan().choices()) or [_catalog.placeholder()]
        return _inputs((labels, {"tooltip": "Filters 8bf-host can run on this machine."}))

    @classmethod
    def IS_CHANGED(cls, filter, settings, preset_file="", **_rest):
        filter_id = _catalog.choices().get(filter, filter)
        return changed(filter_id, settings, preset_file)

    def apply(self, image, filter, settings, depth, **options):
        return (run(resolve(filter), image, settings, depth, **options),)


class EightBfFilterByName:
    """Runs a filter named in text, for workflows shared between machines."""

    CATEGORY = CATEGORY
    DESCRIPTION = (
        "Run a .8bf filter given by its id, name, Category/Name or plug-in file, as "
        "8bf-host's command line accepts them."
    )
    FUNCTION = "apply"
    RETURN_TYPES = ("IMAGE",)

    @classmethod
    def INPUT_TYPES(cls):
        return _inputs(("STRING", {"default": "", "tooltip": "For example Acme/Unsharp Mask."}))

    @classmethod
    def IS_CHANGED(cls, filter, settings, preset_file="", **_rest):
        return changed((filter or "").strip(), settings, preset_file)

    def apply(self, image, filter, settings, depth, **options):
        name = (filter or "").strip()
        if not name:
            raise bridge.BridgeError("Name the filter to run.")
        return (run(name, image, settings, depth, **options),)


NODE_CLASS_MAPPINGS = {
    "EightBfFilter": EightBfFilter,
    "EightBfFilterByName": EightBfFilterByName,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "EightBfFilter": "8bf Filter",
    "EightBfFilterByName": "8bf Filter (by Name)",
}

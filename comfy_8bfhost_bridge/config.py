"""The bridge's configuration file and stored filter presets.

ComfyUI has no settings store for custom nodes, so the configuration is a
small JSON file in ComfyUI's user folder:

    {"host_path": "/opt/8bf-host/8bf-host", "folders": ["/data/filters"]}

Both keys are optional. A missing file means the defaults.
"""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List

FOLDER_NAME = "8bfhost_bridge"
CONFIG_NAME = "config.json"


@dataclass
class Config:
    # The 8bf-host program. Empty means "look for it".
    host_path: str = ""
    # Extra filter folders. 8bf-host adds the usual Adobe folders and its own
    # configured ones by itself.
    folders: List[str] = field(default_factory=list)


def load(path):
    """Read the configuration file, falling back to defaults."""
    config = Config()
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return config
    except (OSError, ValueError) as exc:
        sys.stderr.write("comfy_8bfhost_bridge: ignoring %s: %s\n" % (path, exc))
        return config
    if not isinstance(payload, dict):
        sys.stderr.write("comfy_8bfhost_bridge: ignoring %s: not a JSON object\n" % path)
        return config
    if isinstance(payload.get("host_path"), str):
        config.host_path = os.path.expanduser(payload["host_path"].strip())
    if isinstance(payload.get("folders"), list):
        config.folders = [
            os.path.expanduser(entry.strip())
            for entry in payload["folders"]
            if isinstance(entry, str) and entry.strip()
        ]
    return config


class PresetStore:
    """Where each filter's last settings live, as 8bf-host preset files.

    8bf-host writes the preset after a run (``--save-preset``) and reads it
    for the next one (``--preset``), which is what "last used" settings use.
    """

    def __init__(self, directory):
        self.directory = directory

    def path(self, filter_id):
        os.makedirs(self.directory, exist_ok=True)
        return os.path.join(self.directory, preset_name(filter_id))

    def existing(self, filter_id):
        """Return the preset path when one was saved, else None."""
        path = self.path(filter_id)
        return path if os.path.isfile(path) and os.path.getsize(path) > 0 else None


def preset_name(filter_id):
    """A file name for a filter's preset, safe to use and unique per filter.

    8bf-host's ids are already safe. Names typed into the by-name node are
    not, and "Acme/Sharpen" and "Acme_Sharpen" must not share a preset, so
    anything that had to be changed gets a hash of the original.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", filter_id)[:80]
    if safe != filter_id or not safe:
        safe += "-" + hashlib.sha256(filter_id.encode("utf-8")).hexdigest()[:12]
    return safe + ".json"


def digest(path):
    """A hash of a file's contents, or "" when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""

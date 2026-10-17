"""Finding the 8bf-host program and running it.

The nodes do not load filters themselves. Every filter runs in 8bf-host
(https://github.com/distortionary-labs/8bf-host), which the user installs
separately. This module builds its command lines and runs them; nothing
here imports ComfyUI, so the caller passes a callback for cancelling.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List

DOWNLOAD_URL = "https://github.com/distortionary-labs/8bf-host/releases"
PROGRAM = "8bf-host"
# Names the program when no path is configured.
ENV_VARIABLE = "EIGHTBF_HOST"

# 8bf-host's exit codes (see `8bf-host --help`).
EXIT_CANCELLED = 130

# How long `8bf-host list` may take. A first scan of a large folder reads
# every plug-in file; later ones use 8bf-host's cache.
LIST_TIMEOUT = 300.0


class HostError(RuntimeError):
    """8bf-host is missing or failed."""


@dataclass
class Result:
    status: str
    message: str = ""
    stdout: str = ""
    log: List[str] = field(default_factory=list)

    @property
    def ok(self):
        return self.status == "ok"


def _candidates(home):
    suffix = ".exe" if sys.platform == "win32" else ""
    return [
        os.path.join(home, ".cargo", "bin", PROGRAM + suffix),
        os.path.join(home, ".local", "bin", PROGRAM + suffix),
    ]


def find_executable(configured="", which=shutil.which, home=None, environ=None):
    """Return the 8bf-host to use, or "" when there is none.

    A configured path wins, then $EIGHTBF_HOST. Otherwise look on PATH and
    in the folders ``cargo install`` and user installs use, since ComfyUI
    started from a desktop launcher often has a shorter PATH than a terminal.
    """
    if configured:
        return configured
    from_env = (os.environ if environ is None else environ).get(ENV_VARIABLE, "")
    if from_env:
        return from_env
    found = which(PROGRAM)
    if found:
        return found
    for path in _candidates(home or os.path.expanduser("~")):
        if os.path.isfile(path):
            return path
    return ""


def require_executable(configured="", config_hint="the bridge's config.json", which=shutil.which):
    executable = find_executable(configured)
    if not executable:
        raise HostError(
            "8bf-host was not found. Download it from %s, then put it on PATH, set "
            "EIGHTBF_HOST, or set host_path in %s." % (DOWNLOAD_URL, config_hint)
        )
    if not os.path.isfile(executable) and not which(executable):
        raise HostError(
            "8bf-host is set to %s, which does not exist. Check host_path in %s and "
            "the EIGHTBF_HOST environment variable." % (executable, config_hint)
        )
    return executable


def _option(name, value):
    # One argument, so a value that starts with "-" is not read as an option.
    return "--%s=%s" % (name, value)


def folder_args(folders):
    return [_option("plugin-dir", folder) for folder in folders]


def list_command(executable, folders, rescan=False):
    return [executable, "list", "--json"] + folder_args(folders) + (["--rescan"] if rescan else [])


def run_command(
    executable,
    folders,
    filter_id,
    input_path,
    output_path,
    dialog=True,
    preset=None,
    save_preset=None,
    mask=None,
    rect=None,
    foreground=(0, 0, 0),
    background=(255, 255, 255),
    resolution=72.0,
    allow_8bit_fallback=True,
):
    """Build an ``8bf-host run`` command. ``rect`` is ``(x, y, width, height)``.

    Options come first and the filter and files after ``--``, so a filter
    name or path that starts with "-" cannot turn into an option.
    """
    command = [executable, "run", "--verbose"]
    command += folder_args(folders)
    if dialog:
        command.append("--dialog")
    if preset:
        command.append(_option("preset", preset))
    if save_preset:
        command.append(_option("save-preset", save_preset))
    if mask:
        command.append(_option("mask", mask))
    if rect:
        command.append(_option("rect", "%d,%d,%d,%d" % tuple(rect)))
    command += [
        _option("foreground", "#%02x%02x%02x" % tuple(foreground)),
        _option("background", "#%02x%02x%02x" % tuple(background)),
        _option("resolution", "%g" % resolution),
    ]
    if not allow_8bit_fallback:
        command.append("--no-8bit-fallback")
    return command + ["--", filter_id, input_path, output_path]


def _popen_flags():
    if sys.platform == "win32":
        # Do not flash a console window for the host.
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _interrupt(process):
    """Ask 8bf-host to stop the filter, as Ctrl+C does in a terminal."""
    if sys.platform == "win32":
        # A process without a console cannot receive Ctrl+C.
        process.terminate()
    else:
        process.send_signal(signal.SIGINT)


def _collect(stream, sink):
    for raw in iter(stream.readline, b""):
        sink.append(raw.decode("utf-8", "replace"))
    stream.close()


def _error_message(lines, returncode):
    for line in reversed(lines):
        if line.startswith("error: "):
            return line[len("error: ") :]
    detail = "\n".join(lines[-15:]).strip()
    return "8bf-host exited with code %s.%s" % (returncode, ("\n\n" + detail) if detail else "")


def _stop(process):
    """Kill 8bf-host if it is still running, so it never outlives its caller."""
    if process.poll() is None:
        process.kill()
        process.wait()


def run(command, pump=lambda: None, cancelled=lambda: False, grace=10.0, cwd=None, timeout=None):
    """Run 8bf-host and wait for it, calling ``pump`` while it runs.

    When ``cancelled()`` turns true, or ``timeout`` seconds pass, the host is
    asked to stop the filter; if it has not exited ``grace`` seconds later it
    is killed.
    """
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            **_popen_flags(),
        )
    except OSError as exc:
        return Result("error", "Could not start 8bf-host (%s): %s" % (command[0], exc))
    stdout, stderr = [], []
    readers = [
        threading.Thread(target=_collect, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_collect, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = None if timeout is None else time.monotonic() + timeout
    timed_out = False
    cancel_sent = None
    try:
        while process.poll() is None:
            pump()
            if cancel_sent is None:
                timed_out = deadline is not None and time.monotonic() > deadline
                if timed_out or cancelled():
                    _interrupt(process)
                    cancel_sent = time.monotonic()
            if cancel_sent is not None and time.monotonic() - cancel_sent > grace:
                _stop(process)
                break
            time.sleep(0.03)
    finally:
        # An exception from pump or cancelled must not leave 8bf-host running.
        _stop(process)
    for reader in readers:
        reader.join(5)
    pump()
    log = [line.rstrip() for line in stderr]
    output = "".join(stdout)
    if timed_out:
        message = "8bf-host did not finish within %g seconds." % timeout
        return Result("error", message, output, log)
    if process.returncode == 0:
        return Result("ok", stdout=output, log=log)
    if process.returncode == EXIT_CANCELLED or cancel_sent is not None:
        return Result("cancelled", "Cancelled.", output, log)
    return Result("error", _error_message(log, process.returncode), output, log)


def _objects(payload, key):
    entries = payload.get(key) or []
    if not isinstance(entries, list):
        raise HostError("8bf-host's filter list has a %r that is not a list." % key)
    return [entry for entry in entries if isinstance(entry, dict)]


def list_filters(executable, folders, rescan=False, runner=run, timeout=LIST_TIMEOUT):
    """Return ``(filters, problems)`` as 8bf-host's ``list --json`` reports them."""
    result = runner(list_command(executable, folders, rescan), timeout=timeout)
    if not result.ok:
        raise HostError(result.message or "8bf-host could not list the filters.")
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise HostError("8bf-host printed a filter list that is not JSON: %s" % exc) from exc
    if not isinstance(payload, dict):
        raise HostError("8bf-host printed a filter list that is not a JSON object.")
    return _objects(payload, "filters"), _objects(payload, "problems")

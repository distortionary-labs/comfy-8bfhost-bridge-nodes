import os
import shutil
import struct
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")

# Import the package directly; the folder's own __init__.py is ComfyUI's entry point.
sys.path.insert(0, ROOT)

LIBRARY_SUFFIX = ".dylib" if sys.platform == "darwin" else ".so"


def _compiler():
    for name in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if name and shutil.which(name):
            return name
    return None


def _fourcc(text):
    return struct.unpack(">I", text.encode("ascii"))[0]


def _pascal(text):
    raw = text.encode("latin-1")
    return bytes([len(raw)]) + raw


def pipl_sidecar(name, category, entry):
    """A PiPL resource describing one filter, for a native library's .pipl file."""
    properties = [
        ("kind", struct.pack("<I", _fourcc("8BFM"))),
        ("name", _pascal(name)),
        ("catg", _pascal(category)),
        ("8664", entry.encode("ascii") + b"\0"),
    ]
    out = bytearray(struct.pack("<hii", 1, 0, len(properties)))
    for key, value in properties:
        out += struct.pack("<IIii", _fourcc("8BIM"), _fourcc(key), 0, len(value))
        out += value + b"\0" * (((len(value) + 3) & ~3) - len(value))
    return bytes(out)


@pytest.fixture(scope="session")
def eightbf_host():
    """A dynamically linked 8bf-host, from $EIGHTBF_HOST.

    Release builds for Linux are static and cannot load native .so
    filters, so these tests need a build from ``cargo build``.
    """
    path = os.environ.get("EIGHTBF_HOST")
    if not path:
        pytest.skip("set EIGHTBF_HOST to a dynamically linked 8bf-host to run this test")
    return path


@pytest.fixture(scope="session")
def filter_folder(tmp_path_factory):
    """A folder with the test filter built natively, as "Tests/Invert" and "Tests/Error"."""
    compiler = _compiler()
    if compiler is None or sys.platform == "win32":
        pytest.skip("needs a C compiler on a Unix host")
    folder = tmp_path_factory.mktemp("plugins")
    source = os.path.join(FIXTURES, "testfilter.c")
    for name, entry in (("Invert", "InvertMain"), ("Error", "ErrorMain")):
        library = folder / (name.lower() + LIBRARY_SUFFIX)
        command = [compiler, "-shared", "-fPIC", "-O1", "-o", str(library), source]
        subprocess.run(command, check=True)
        (folder / (library.name + ".pipl")).write_bytes(pipl_sidecar(name, "Tests", entry))
    return str(folder)

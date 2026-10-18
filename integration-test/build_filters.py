"""Build the test filter into a folder, as "Tests/Invert" and "Tests/Error".

Usage: python build_filters.py FOLDER

Expects the repository's tests/ folder next to this script, as the
Dockerfile lays it out, and reuses its PiPL helper.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tests"))

from conftest import FIXTURES, LIBRARY_SUFFIX, pipl_sidecar  # noqa: E402

FILTERS = (("Invert", "InvertMain"), ("Error", "ErrorMain"))


def main(folder):
    os.makedirs(folder, exist_ok=True)
    source = os.path.join(FIXTURES, "testfilter.c")
    for name, entry in FILTERS:
        library = os.path.join(folder, name.lower() + LIBRARY_SUFFIX)
        subprocess.run(["cc", "-shared", "-fPIC", "-O1", "-o", library, source], check=True)
        with open(library + ".pipl", "wb") as out:
            out.write(pipl_sidecar(name, "Tests", entry))
        print("built", library)


if __name__ == "__main__":
    main(sys.argv[1])

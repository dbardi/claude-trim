"""Load the hyphenated source files as importable modules.

`hook-trim.py` and `trim-output.py` are not valid Python identifiers, so they
cannot be imported normally. Keeping the source filenames identical to the
installed filenames means installation is a plain copy with no rename step to
get wrong, which is worth this small amount of loader machinery.
"""
import importlib.util
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def load(filename):
    path = SRC / filename
    spec = importlib.util.spec_from_file_location(
        path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

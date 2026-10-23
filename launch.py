"""Small standard-library launcher with actionable dependency diagnostics."""
from pathlib import Path
import importlib.util
import runpy
import sys


def main():
    if sys.version_info < (3, 9):
        print("TCG RENDER requires Python 3.9 or newer.")
        return 2
    if not any(value in ("-h", "--help", "--version") for value in sys.argv[1:]):
        missing = [package for module, package in (("PIL", "Pillow"), ("UnityPy", "UnityPy"), ("lz4", "lz4"))
                   if importlib.util.find_spec(module) is None]
        if missing:
            print("TCG RENDER: missing Python dependencies: " + ", ".join(missing))
            print("Selected Python: " + sys.executable)
            print("Run setup.cmd to prepare the environment. See README.md.")
            return 2
    runpy.run_path(str(Path(__file__).with_name("render_cards.py")), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

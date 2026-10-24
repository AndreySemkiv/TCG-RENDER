"""Small standard-library launcher with actionable dependency diagnostics."""
from pathlib import Path
import importlib.util
import runpy
import subprocess
import sys


def main():
    if sys.version_info < (3, 9):
        print("TCG RENDER requires Python 3.9 or newer.")
        return 2
    skip_update = '--skip-update-check' in sys.argv[1:]
    if skip_update:
        sys.argv.remove('--skip-update-check')
    if len(sys.argv) == 1 and sys.stdin.isatty() and not skip_update:
        from pocket_renderer import __version__
        from pocket_renderer.updater import offer_update, RecoveryError
        root = Path(__file__).resolve().parent
        try:
            updated = offer_update(root, __version__)
        except RecoveryError:
            return 2
        if updated:
            python = root/'.venv'/('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            return subprocess.call([str(python) if python.is_file() else sys.executable,
                                    str(root/'launch.py'), '--skip-update-check'])
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

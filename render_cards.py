"""Entry point: use the prepared local Python environment shown in BATCH-RENDER.md."""
from pocket_renderer.cli import main
import sys

if __name__ == '__main__':
    if len(sys.argv) == 1:
        from pocket_renderer.menu import run_menu
        raise SystemExit(run_menu())
    raise SystemExit(main())

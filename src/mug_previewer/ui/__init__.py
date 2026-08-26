"""Desktop UI entry point for Mug Previewer."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the desktop UI without importing Tkinter for non-UI callers."""
    parser = argparse.ArgumentParser(prog="python -m mug_previewer.ui")
    parser.add_argument("--dataset-root", type=Path, help="Override the workflow-v6 dataset root.")
    args = parser.parse_args(argv)
    from .app import launch
    return launch(dataset_root=args.dataset_root)


if __name__ == "__main__":
    raise SystemExit(main())

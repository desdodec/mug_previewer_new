"""Desktop UI entry point for Mug Previewer."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the desktop UI without importing Tkinter for non-UI callers."""
    parser = argparse.ArgumentParser(prog="python -m mug_previewer.ui")
    parser.add_argument("--preprocessed", type=Path, help="Use cached previews from a preprocessing output directory.")
    parser.add_argument("--dataset-root", type=Path, help="Override the workflow-v6 dataset root.")
    args = parser.parse_args(argv)
    from .workspace_app import launch
    return launch(dataset_root=args.dataset_root, preprocessed=args.preprocessed)


if __name__ == "__main__":
    raise SystemExit(main())

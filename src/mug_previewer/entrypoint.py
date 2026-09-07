"""Package entry point that routes the desktop UI to the unified workspace."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[-1] == "ui":
        parser = argparse.ArgumentParser(prog="mug-previewer")
        parser.add_argument("--dataset-root", type=Path)
        parser.add_argument("--preprocessed", type=Path)
        parser.add_argument("command", choices=("ui",))
        parsed = parser.parse_args(args)
        from .config import load_settings
        from .ui.workspace_app import launch

        root = parsed.dataset_root or load_settings().dataset_root
        return launch(dataset_root=root, preprocessed=parsed.preprocessed)

    from .cli import main as cli_main
    return cli_main(args)

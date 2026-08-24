"""Configuration precedence: CLI, environment, local config, project default."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DATASET_ROOT_ENV = "MUG_PREVIEWER_DATASET_ROOT"
LOCAL_CONFIG_ENV = "MUG_PREVIEWER_LOCAL_CONFIG"

@dataclass(frozen=True)
class Settings:
    dataset_root: Path | None
    source: str

def _toml_root(path: Path) -> str | None:
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    value = data.get("datasets", {}).get("root")
    return str(value).strip() if value is not None and str(value).strip() else None

def load_settings(*, dataset_root: Path | str | None = None, environ: Mapping[str, str] | None = None, local_config_path: Path | None = None, project_config_path: Path | None = None, working_directory: Path | None = None) -> Settings:
    if dataset_root is not None and str(dataset_root).strip():
        return Settings(Path(dataset_root).expanduser(), "CLI argument")
    environment = os.environ if environ is None else environ
    value = environment.get(DATASET_ROOT_ENV, "").strip()
    if value:
        return Settings(Path(value).expanduser(), DATASET_ROOT_ENV)
    directory = working_directory or Path.cwd()
    local = local_config_path or Path(environment.get(LOCAL_CONFIG_ENV, directory / "local_config.toml"))
    value = _toml_root(local)
    if value:
        return Settings(Path(value).expanduser(), f"local config {local}")
    dotenv = directory / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{DATASET_ROOT_ENV}="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return Settings(Path(value).expanduser(), f"local environment file {dotenv}")
    default = project_config_path or Path(__file__).resolve().parents[2] / "config" / "default.toml"
    value = _toml_root(default)
    return Settings(Path(value).expanduser() if value else None, "project default" if value else "no dataset root configured")

"""Non-destructive manual artwork lifecycle and desktop editor integration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .preprocess import validate_manual_svg
from .canonical_svg import serialized


@dataclass(frozen=True)
class ManualSvgWorkspace:
    generated_svg: Path | None
    working_svg: Path | None
    eligible: bool
    root: Path | None = None
    dataset_id: str = ""
    street_id: str = ""

    @classmethod
    def from_record(cls, record, records=()):
        from .canonical_svg import history_paths
        if record is None or record.svg_path is None:
            return cls(None, None, False)
        canonical = record.svg_path
        original, _ = history_paths(canonical)
        root = next((p for p in canonical.parents if (p / 'preprocess_index.json').is_file()), None)
        state = record.state.value if record.state else ""
        return cls(original, canonical,
                   state in {"AUTO_APPROVED", "MANUAL_REVIEW", "MANUAL_APPROVED"},
                   root, record.dataset_id, record.street_id)

    @property
    def can_edit(self) -> bool:
        return self.eligible and self.working_svg is not None and self.working_svg.is_file()

    @property
    def has_working(self) -> bool:
        return self.working_svg is not None and self.working_svg.is_file()

    def create_or_get_working_edit(self) -> Path:
        from .canonical_svg import prepare_edit
        if not self.can_edit or self.root is None:
            raise ValueError("Asset integrity error: canonical SVG is missing or this street is not eligible for editing.")
        return prepare_edit(self.root, self.dataset_id, self.street_id)


def find_inkscape_executable(configured: Path | None = None) -> Path | None:
    """An explicit session override wins; otherwise PATH, then common installs."""
    if configured is not None:
        return Path(configured) if Path(configured).is_file() else None
    detected = shutil.which("inkscape")
    if detected:
        return Path(detected)
    if os.name == "nt":
        registered = _registered_inkscape_executable()
        if registered is not None:
            return registered
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if not base:
                continue
            for suffix in ("Inkscape/bin/inkscape.exe", "Inkscape/inkscape.exe", "Programs/Inkscape/bin/inkscape.exe"):
                candidate = Path(base) / suffix
                if candidate.is_file():
                    return candidate
    return None


def _registered_inkscape_executable() -> Path | None:
    """Windows App Paths supports installations on non-system drives."""
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\inkscape.exe",
                                    0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    candidate = Path(os.path.expandvars(str(value).strip('"')))
                    if candidate.is_file():
                        return candidate
            except OSError:
                continue
    return None


def launch_inkscape(workspace: ManualSvgWorkspace, configured: Path | None = None) -> Path:
    working = workspace.create_or_get_working_edit()
    executable = find_inkscape_executable(configured)
    if executable is None:
        raise FileNotFoundError("Inkscape was not found. Choose the Inkscape executable to continue.")
    subprocess.Popen([str(executable), str(working)], shell=False)
    return working


def open_artwork_folder(workspace: ManualSvgWorkspace) -> None:
    source = workspace.working_svg
    if source is None or not source.parent.is_dir():
        raise ValueError("Artwork folder is unavailable.")
    open_local_path(source.parent)


def open_local_path(path: Path) -> None:
    """Open an existing local folder or report using the platform default."""
    folder = Path(path).resolve()
    if not folder.exists():
        raise ValueError(f"Path is unavailable: {folder}")
    if os.name == "nt":
        os.startfile(str(folder))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)], shell=False)


class EditSaveMonitor:
    """Debounce canonical saves without transforming the editor's bytes."""
    def __init__(self, workspace, dataset, street, root, on_detected=None):
        from .canonical_svg import history_paths
        self.workspace, self.dataset, self.street, self.root = workspace, dataset, street, root
        workspace.create_or_get_working_edit()
        _, good = history_paths(workspace.working_svg)
        self.accepted = good.read_bytes()
        self.on_detected = on_detected
        self.pending = None

    def poll(self):
        from .canonical_svg import accept_saved_svg, history_paths
        path = self.workspace.working_svg
        payload = path.read_bytes() if path.exists() else b''
        if payload == self.accepted:
            self.pending = None
            return False
        if payload != self.pending:
            self.pending = payload
            if self.on_detected:
                self.on_detected()
            return False
        try:
            accept_saved_svg(self.root, self.dataset, self.street)
        finally:
            _, good = history_paths(path)
            self.accepted = good.read_bytes()
            self.pending = None
        # A synchronous Preview/Export may already have accepted this save.
        return True


@serialized
def revert_generated(dataset, street, root):
    from .preprocess import (_load_index, _record_key, INDEX_FILENAME, _write_index,
                             _write_preview, _atomic_asset_write)
    from .canonical_svg import history_paths
    root = Path(root)
    records = _load_index(root / INDEX_FILENAME)
    record = next(r for r in records if _record_key(r) == (dataset.id, street.id))
    canonical = root / record['svg_path']
    generated, good = history_paths(canonical)
    payload = generated.read_bytes()
    validate_manual_svg(payload, dataset, street)
    preview = root / record['preview_path']
    with tempfile.TemporaryDirectory(dir=root) as staging:
        staged = Path(staging) / 'preview.png'
        _write_preview(payload, staged)
        preview_bytes = staged.read_bytes()
    previous = {p: p.read_bytes() if p.exists() else None for p in (canonical, preview, good)}
    record['production_state'] = record.get('generated_production_state', 'MANUAL_REVIEW')
    for key in ('approved_svg_path', 'approved_svg_sha256', 'approved_at'):
        record.pop(key, None)
    try:
        _atomic_asset_write(canonical, payload)
        _atomic_asset_write(preview, preview_bytes)
        _atomic_asset_write(good, payload)
        _write_index(root / INDEX_FILENAME, records)
    except Exception:
        for path, old in previous.items():
            if old is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_asset_write(path, old)
        raise

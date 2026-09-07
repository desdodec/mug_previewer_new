"""Non-destructive manual artwork lifecycle and desktop editor integration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from .preprocess import approve_manual_svg, validate_manual_svg
from .svg_edit_repair import correct_edited_svg, corrected_svg


@dataclass(frozen=True)
class ManualSvgWorkspace:
    generated_svg: Path | None
    working_svg: Path | None
    corrected_svg: Path | None
    approved_svg: Path | None
    eligible: bool
    protected_svg_paths: tuple[Path, ...] = ()

    @classmethod
    def from_record(cls, record, records=()):
        if record is None:
            return cls(None, None, None, None, False)
        generated = record.generated_svg_path
        state = record.state.value if record.state else ""
        if generated is None and state != "MANUAL_APPROVED":
            generated = record.editable_svg_path or record.svg_path
        working = generated.with_name(generated.stem.removesuffix(".generated") + "_edit.svg") if generated else None
        corrected = working.parent / "corrected" / working.name if working else None
        approved = record.approved_svg_path
        if approved is None and generated:
            approved = generated.with_name(generated.stem.removesuffix(".generated") + ".approved.svg")
        return cls(generated, working, corrected, approved,
                   state in {"MANUAL_REVIEW", "MANUAL_APPROVED"},
                   tuple(path for item in records for path in
                         (item.generated_svg_path, item.svg_path, item.approved_svg_path) if path))

    @property
    def can_edit(self) -> bool:
        return self.eligible and self.generated_svg is not None and self.generated_svg.is_file()

    @property
    def has_working(self) -> bool:
        return self.working_svg is not None and self.working_svg.is_file()

    @property
    def correction_current(self) -> bool:
        """Reject invalid or outdated corrections without running a renderer."""
        if not self.can_edit or not self.has_working or not self.corrected_svg.is_file():
            return False
        try:
            expected = corrected_svg(self.generated_svg.read_text(encoding="utf-8-sig"),
                                     self.working_svg.read_text(encoding="utf-8-sig"))
            payload = self.corrected_svg.read_bytes()
            validate_manual_svg(payload)
            return payload.decode("utf-8").replace("\r\n", "\n") == expected
        except (OSError, ValueError, ET.ParseError):
            return False

    def _require_editable(self):
        if not self.can_edit:
            raise ValueError("Asset integrity error: canonical generated SVG is missing or this street is not eligible for manual editing.")
        paths = [self.generated_svg, self.working_svg, self.corrected_svg, self.approved_svg]
        if len({p.resolve() for p in paths}) != len(paths):
            raise ValueError("Asset integrity error: artwork roles must use separate files.")
        for target in (self.working_svg, self.corrected_svg):
            for protected in (*self.protected_svg_paths, self.generated_svg, self.approved_svg):
                if target.resolve() == protected.resolve() or (target.exists() and protected.exists() and target.samefile(protected)):
                    raise ValueError("Asset integrity error: edit path collides with indexed artwork.")

    def create_or_get_working_edit(self) -> Path:
        self._require_editable()
        if self.working_svg.exists():
            if not self.working_svg.is_file():
                raise ValueError("Working edit path is not a file.")
            return self.working_svg
        # Publish a fully written copy without ever replacing an existing edit.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.working_svg.parent, prefix=".working-",
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(self.generated_svg.read_bytes())
            try:
                os.link(temporary, self.working_svg)
            except FileExistsError:
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return self.working_svg

    def repair(self) -> Path:
        self._require_editable()
        if not self.has_working:
            raise ValueError("Create and save a working edit before repairing it.")
        return correct_edited_svg(self.generated_svg, self.working_svg, self.corrected_svg, replace=True)

    def approve(self, dataset, street, preprocessed):
        self._require_editable()
        if not self.correction_current:
            raise ValueError("Repair the latest working edit before approving the corrected SVG.")
        validate_manual_svg(self.corrected_svg.read_bytes(), dataset, street)
        return approve_manual_svg(dataset, street, preprocessed, self.corrected_svg)


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
    source = workspace.generated_svg or workspace.approved_svg
    if source is None or not source.parent.is_dir():
        raise ValueError("Artwork folder is unavailable.")
    folder = source.parent.resolve()
    if os.name == "nt":
        os.startfile(str(folder))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)], shell=False)

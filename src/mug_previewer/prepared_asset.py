"""Resolve prepared front artwork without regenerating reviewed faces.

Canonical SVG remains the preferred source.  A cached 495x462 preview is a
read-only production recovery source only when the indexed SVG is genuinely
missing.  This preserves the last rendered face when older prepared sets have
lost their vector files, without pretending that the raster cache is editable.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from pathlib import Path

from PIL import Image

from .preprocess import validate_manual_svg
from .rendering.face import FRONT_PANEL_PX
from .rendering.svg_raster import rasterize_face_svg


@dataclass(frozen=True)
class PreparedFaceSource:
    path: Path
    kind: str
    digest: str

    @property
    def is_svg(self) -> bool:
        return self.kind == "canonical_svg"

    @property
    def review_path(self) -> Path | None:
        """Legacy review hashes are SVG hashes, never cached-preview hashes."""
        return self.path if self.is_svg else None


def _value(record, name: str):
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _contained_path(root: Path, value) -> Path | None:
    if value is None:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return root.resolve() / relative


def _valid_preview_payload(path: Path) -> tuple[bytes, str]:
    payload = path.read_bytes()
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            size = image.size
    except (OSError, ValueError) as error:
        raise ValueError(f"Cached face preview is unreadable: {error}") from error
    if size != FRONT_PANEL_PX:
        raise ValueError(
            f"Cached face preview must be {FRONT_PANEL_PX[0]}x{FRONT_PANEL_PX[1]} pixels; got {size[0]}x{size[1]}."
        )
    return payload, hashlib.sha256(payload).hexdigest()


def resolve_prepared_face_source(root: Path | str, record) -> PreparedFaceSource:
    """Return the immutable front source for a prepared record.

    Existing SVGs always win and must validate.  We never hide a corrupt or
    partially-saved SVG behind an older PNG.  The PNG recovery path is used only
    when the indexed SVG does not exist at all.
    """
    root = Path(root).resolve()
    svg_path = _contained_path(root, _value(record, "svg_path"))
    if svg_path is not None and svg_path.is_file():
        payload = svg_path.read_bytes()
        validate_manual_svg(payload)
        return PreparedFaceSource(svg_path, "canonical_svg", hashlib.sha256(payload).hexdigest())

    preview_path = _contained_path(root, _value(record, "preview_path"))
    if preview_path is None or not preview_path.is_file():
        raise ValueError("Authoritative SVG missing and no cached face preview is available.")
    _, digest = _valid_preview_payload(preview_path)
    return PreparedFaceSource(preview_path, "cached_preview", digest)


def load_prepared_face_image(source: PreparedFaceSource) -> Image.Image:
    """Load the exact 495x462 front panel represented by ``source``."""
    if source.is_svg:
        return rasterize_face_svg(source.path)
    payload, digest = _valid_preview_payload(source.path)
    if digest != source.digest:
        raise ValueError("Cached face preview changed while it was being loaded.")
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        return image.convert("RGBA").copy()

"""Artwork renderers for the typed Mug Previewer domain."""

# Install the shared front-title fit policy before callers import symbols from
# ``rendering.face``.  The face module remains authoritative for geometry and
# native styling; title_guardrails only replaces the two front composition
# entry points so calibration, preprocess, previews and exports use one layout.
from . import face as _face
from .title_guardrails import install as _install_title_guardrails

_install_title_guardrails(_face)

del _install_title_guardrails

"""Artwork renderers for the typed Mug Previewer domain."""

# Install the shared front-title fit policy before callers import symbols from
# ``rendering.face``.  The face module remains authoritative for geometry and
# native styling; title_guardrails only replaces the two front composition
# entry points so calibration, preprocess, previews and exports use one layout.
from . import face as _face
from .title_guardrails import install as _install_title_guardrails

_install_title_guardrails(_face)

del _install_title_guardrails

# Rear calibration profiles can contain vertical offsets that move required
# attribution beyond the panel edge.  Clamp only the final text baselines; map
# framing, highlight geometry and the user's typography scales remain intact.
from . import context_map as _context_map
from .rear_attribution_guardrails import install as _install_rear_attribution_guardrails

_install_rear_attribution_guardrails(_context_map)

del _install_rear_attribution_guardrails

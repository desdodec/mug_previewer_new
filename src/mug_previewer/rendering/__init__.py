"""Artwork renderers for the typed Mug Previewer domain."""

# Install the shared front-title fit policy before callers import symbols from
# ``rendering.face``.  The face module remains authoritative for geometry and
# native styling; title_guardrails only replaces the two front composition
# entry points so calibration, preprocess, previews and exports use one layout.
from . import face as _face
from .title_guardrails import install as _install_title_guardrails

_install_title_guardrails(_face)

del _install_title_guardrails

from . import context_map as _context_map

# Verified local-OSM context SVGs already contain an individually fitted crop,
# high-resolution embedded raster and vector street highlight. Preserve that
# source frame/aspect before applying attribution safety rails.
from .rear_source_frame import install as _install_rear_source_frame

_install_rear_source_frame(_context_map)

del _install_rear_source_frame

# Rear calibration profiles can contain vertical offsets that move required
# attribution beyond the panel edge.  Clamp only the final text baselines; map
# framing, highlight geometry and the user's typography scales remain intact.
from .rear_attribution_guardrails import install as _install_rear_attribution_guardrails

_install_rear_attribution_guardrails(_context_map)

del _install_rear_attribution_guardrails

# Carry a presentation-only rear artwork scale through the completed rear
# image. Provider composition applies the physical enlargement later, so map
# geography, native raster resolution and attribution layout stay unchanged.
from .rear_artwork_scale import install as _install_rear_artwork_scale

_install_rear_artwork_scale(_context_map)

del _install_rear_artwork_scale

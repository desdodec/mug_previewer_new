"""Provider-neutral Mug Previewer V2 geometry and rendering."""

from .models import (
    CameraPose,
    GENERIC_11OZ_CALIBRATION,
    MugCalibration,
    MugPreviewV2Options,
    PreviewScene,
    PreviewV2Error,
    PreviewV2Mode,
    PreviewV2View,
)
from .projection import (
    ProjectionDiagnostics,
    build_circumference_strip,
    circumference_width_px,
    project_wrap_v2,
    projection_diagnostics,
)
from .renderer import MugPreviewV2Result, render_mug_preview_v2, render_mug_preview_v2_result

__all__ = [
    "CameraPose",
    "GENERIC_11OZ_CALIBRATION",
    "MugCalibration",
    "MugPreviewV2Options",
    "PreviewScene",
    "PreviewV2Error",
    "PreviewV2Mode",
    "PreviewV2View",
    "ProjectionDiagnostics",
    "build_circumference_strip",
    "circumference_width_px",
    "project_wrap_v2",
    "projection_diagnostics",
    "MugPreviewV2Result",
    "render_mug_preview_v2",
    "render_mug_preview_v2_result",
]

"""Provider-ready exports derived from canonical wrap masters.

This package is deliberately downstream from :mod:`mug_previewer.rendering`.
Provider profiles transform a completed master; they never alter canonical
panel geometry or call a renderer themselves.
"""

from .export import (
    ProviderExportError,
    ProviderExportOptions,
    ProviderExportResult,
    export_wrap,
    export_wrap_result,
    provider_export_filename,
    save_provider_png,
)
from .providers import (
    GELATO_WHITE_11OZ_CERAMIC_MUG,
    AlphaHandling,
    CroppingPolicy,
    PaddingPolicy,
    ProviderExportSpec,
    ScalingPolicy,
)

__all__ = [
    "AlphaHandling",
    "CroppingPolicy",
    "GELATO_WHITE_11OZ_CERAMIC_MUG",
    "PaddingPolicy",
    "ProviderExportError",
    "ProviderExportOptions",
    "ProviderExportResult",
    "ProviderExportSpec",
    "ScalingPolicy",
    "export_wrap",
    "export_wrap_result",
    "provider_export_filename",
    "save_provider_png",
]

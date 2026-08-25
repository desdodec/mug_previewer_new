"""Production template-v2 composition for completed front and rear panels.

The legacy ``mug_toolkit.template_v2`` layout measured the source PSD at
2362 x 1063 px (20 x 9 cm at 300 ppi). Its two visible, handle-side print
zones are 945 px wide. This module deliberately owns only placement: the
V28 front and metric rear renderers remain independent.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from ..datasets.models import Dataset, StreetRecord
from .context_map import ContextRenderResult, render_context_map_result
from .face import FaceRenderOptions, render_face


@dataclass(frozen=True)
class PixelBox:
    """An integer rectangle in master-artwork pixels."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True)
class WrapLayout:
    """Measured production geometry for one full-wrap master."""

    canvas_width_px: int
    canvas_height_px: int
    front_box: PixelBox
    rear_box: PixelBox
    bleed_px: int
    seam_zone: PixelBox | None
    dpi: int = 300


# Measured from legacy templates/mug-template.psd through
# mug_toolkit.template_v2.TEMPLATE_V2_LAYOUT. The 472 px centre interval is
# retained as the handle/seam exclusion zone between the two 8 cm print areas.
TEMPLATE_V2_WRAP_LAYOUT = WrapLayout(
    canvas_width_px=2362,
    canvas_height_px=1063,
    front_box=PixelBox(0, 0, 945, 1063),
    rear_box=PixelBox(1417, 0, 945, 1063),
    bleed_px=0,
    seam_zone=PixelBox(945, 0, 472, 1063),
)


@dataclass(frozen=True)
class WrapRenderOptions:
    """Options for canonical full-wrap rendering.

    ``face_options`` is forwarded unchanged to the independent face renderer.
    Debug guides are intentionally off for production masters.
    """

    layout: WrapLayout = TEMPLATE_V2_WRAP_LAYOUT
    face_options: FaceRenderOptions | None = None
    debug_guides: bool = False


@dataclass(frozen=True)
class WrapRenderResult:
    """A master image with the rear framing diagnostics used to make it."""

    image: Image.Image
    front_panel: Image.Image
    rear_panel: Image.Image
    front_placed_box: PixelBox
    rear_placed_box: PixelBox
    context: ContextRenderResult


class WrapRenderError(ValueError):
    """Raised when completed panel artwork cannot be composed safely."""


class WrapComposer:
    """Place completed panel images in the measured template-v2 print zones."""

    def __init__(self, layout: WrapLayout = TEMPLATE_V2_WRAP_LAYOUT) -> None:
        self.layout = layout
        _validate_layout(layout)

    def compose(
        self, front_panel: Image.Image, rear_panel: Image.Image, *, debug_guides: bool = False,
    ) -> tuple[Image.Image, PixelBox, PixelBox]:
        """Return a transparent master and the actual contained panel boxes."""
        master = Image.new(
            "RGBA",
            (self.layout.canvas_width_px, self.layout.canvas_height_px),
            (0, 0, 0, 0),
        )
        front, front_box = _fit_contain(front_panel, self.layout.front_box)
        rear, rear_box = _fit_contain(rear_panel, self.layout.rear_box)
        master.alpha_composite(front, (front_box.x, front_box.y))
        master.alpha_composite(rear, (rear_box.x, rear_box.y))
        if debug_guides:
            _draw_debug_guides(master, self.layout, front_box, rear_box)
        master.info["dpi"] = (self.layout.dpi, self.layout.dpi)
        return master, front_box, rear_box


def render_wrap(
    dataset: Dataset,
    street: StreetRecord,
    options: WrapRenderOptions | None = None,
) -> Image.Image:
    """Render ``street`` as the canonical transparent template-v2 master."""
    return render_wrap_result(dataset, street, options).image


def render_wrap_result(
    dataset: Dataset,
    street: StreetRecord,
    options: WrapRenderOptions | None = None,
) -> WrapRenderResult:
    """Render independent panels, then compose them without retuning either."""
    options = options or WrapRenderOptions()
    face_options = options.face_options or FaceRenderOptions(area=dataset.display_name)
    front_panel = render_face(street, face_options)
    context = render_context_map_result(dataset, street)
    rear_panel = context.image
    image, front_box, rear_box = WrapComposer(options.layout).compose(
        front_panel, rear_panel, debug_guides=options.debug_guides,
    )
    return WrapRenderResult(image, front_panel, rear_panel, front_box, rear_box, context)


def _fit_contain(panel: Image.Image, target: PixelBox) -> tuple[Image.Image, PixelBox]:
    if panel.width <= 0 or panel.height <= 0:
        raise WrapRenderError("Panel dimensions must be positive.")
    if target.width <= 0 or target.height <= 0:
        raise WrapRenderError("Layout panel dimensions must be positive.")
    scale = min(target.width / panel.width, target.height / panel.height)
    width = max(1, round(panel.width * scale))
    height = max(1, round(panel.height * scale))
    x = target.x + (target.width - width) // 2
    y = target.y + (target.height - height) // 2
    image = panel.convert("RGBA")
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image, PixelBox(x, y, width, height)


def _validate_layout(layout: WrapLayout) -> None:
    if layout.canvas_width_px <= 0 or layout.canvas_height_px <= 0 or layout.dpi <= 0:
        raise WrapRenderError("Canvas dimensions and DPI must be positive.")
    for name, box in (("front", layout.front_box), ("rear", layout.rear_box)):
        if (
            box.x < 0 or box.y < 0 or box.width <= 0 or box.height <= 0
            or box.right > layout.canvas_width_px or box.bottom > layout.canvas_height_px
        ):
            raise WrapRenderError(f"{name.capitalize()} panel box must be inside the canvas.")


def _draw_debug_guides(
    image: Image.Image, layout: WrapLayout, front: PixelBox, rear: PixelBox,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    for box, colour in (
        (layout.front_box, (40, 120, 255, 180)),
        (layout.rear_box, (255, 120, 40, 180)),
    ):
        draw.rectangle((box.x, box.y, box.right - 1, box.bottom - 1), outline=colour, width=3)
    if layout.seam_zone is not None:
        seam = layout.seam_zone
        draw.rectangle((seam.x, seam.y, seam.right - 1, seam.bottom - 1), outline=(220, 40, 180, 180), width=3)
    for box, colour in ((front, (30, 220, 120, 180)), (rear, (255, 220, 30, 180))):
        draw.rectangle((box.x, box.y, box.right - 1, box.bottom - 1), outline=colour, width=1)
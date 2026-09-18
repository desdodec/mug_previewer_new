"""Procedural customer and engineering mug previews for Mug Previewer V2."""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from ..preview.mockup import CANONICAL_WRAP_PREVIEW_GEOMETRY
from .models import MugPreviewV2Options, PreviewV2Mode, PreviewV2View
from .projection import ProjectionDiagnostics, project_wrap_v2


@dataclass(frozen=True)
class MugPreviewV2Result:
    image: Image.Image
    diagnostics: ProjectionDiagnostics
    body_bounds_xyxy: tuple[int, int, int, int]
    printable_bounds_xyxy: tuple[int, int, int, int]


def render_mug_preview_v2(
    wrap: Image.Image,
    options: MugPreviewV2Options | None = None,
) -> Image.Image:
    """Render one provider-neutral mug preview without modifying production artwork."""
    return render_mug_preview_v2_result(wrap, options).image


def render_mug_preview_v2_result(
    wrap: Image.Image,
    options: MugPreviewV2Options | None = None,
) -> MugPreviewV2Result:
    options = options or MugPreviewV2Options()
    scene = options.scene
    calibration = options.calibration
    mode = PreviewV2Mode(options.mode)
    view = PreviewV2View(options.view)
    canvas_width, canvas_height = scene.canvas_size

    body_height = scene.body_height_px
    body_width = round(body_height * calibration.body_width_to_height)
    cx = round(canvas_width * scene.centre_x_fraction)
    cy = round(canvas_height * scene.centre_y_fraction)
    left = cx - body_width // 2
    right = left + body_width
    top = cy - body_height // 2
    bottom = top + body_height
    body_bounds = (left, top, right, bottom)

    printable_height = max(1, round(body_height * calibration.printable_height_fraction))
    printable_top = top + (body_height - printable_height) // 2
    printable_bottom = printable_top + printable_height
    printable_bounds = (left, printable_top, right, printable_bottom)

    projected, diagnostics = project_wrap_v2(
        wrap,
        target_size=(body_width, printable_height),
        calibration=calibration,
        camera=options.camera,
        view=view,
        mesh_segments=options.mesh_segments,
    )

    background = Image.new("RGBA", scene.canvas_size, (*scene.background_rgb, 255))
    if mode is PreviewV2Mode.CUSTOMER:
        _draw_customer_environment(background, body_bounds)
    else:
        _draw_engineering_environment(background, body_bounds)

    handle_layer = Image.new("RGBA", scene.canvas_size, (0, 0, 0, 0))
    _draw_handle(handle_layer, body_bounds, calibration, diagnostics, engineering=mode is PreviewV2Mode.ENGINEERING)
    background = Image.alpha_composite(background, handle_layer)

    mug_layer = Image.new("RGBA", scene.canvas_size, (0, 0, 0, 0))
    body_mask = _body_mask(scene.canvas_size, body_bounds, calibration.body_corner_fraction)
    ceramic = _ceramic_body(scene.canvas_size, body_bounds, engineering=mode is PreviewV2Mode.ENGINEERING)
    mug_layer = Image.alpha_composite(mug_layer, ceramic)

    local_mask = body_mask.crop(printable_bounds)
    projected.putalpha(ImageChops.multiply(projected.getchannel("A"), local_mask))
    artwork_layer = Image.new("RGBA", scene.canvas_size, (0, 0, 0, 0))
    artwork_layer.alpha_composite(projected, (left, printable_top))
    mug_layer = Image.alpha_composite(mug_layer, artwork_layer)

    if mode is PreviewV2Mode.CUSTOMER:
        mug_layer = _apply_surface_lighting(mug_layer, body_bounds, body_mask)
        _draw_rim_highlight(mug_layer, body_bounds)

    result = Image.alpha_composite(background, mug_layer)
    if mode is PreviewV2Mode.ENGINEERING or options.show_guides:
        _draw_engineering_guides(
            result, body_bounds, printable_bounds, diagnostics, calibration.wrap_span_degrees, view
        )
    return MugPreviewV2Result(result, diagnostics, body_bounds, printable_bounds)


def _body_mask(
    canvas_size: tuple[int, int],
    bounds: tuple[int, int, int, int],
    corner_fraction: float,
) -> Image.Image:
    left, top, right, bottom = bounds
    radius = max(2, round((bottom - top) * corner_fraction))
    mask = Image.new("L", canvas_size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=radius, fill=255)
    return mask


def _ceramic_body(
    canvas_size: tuple[int, int],
    bounds: tuple[int, int, int, int],
    *,
    engineering: bool,
) -> Image.Image:
    left, top, right, bottom = bounds
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    radius = max(2, round((bottom - top) * 0.055))
    fill = (252, 252, 250, 255) if not engineering else (250, 250, 250, 255)
    outline = (205, 205, 205, 255) if engineering else (225, 224, 220, 255)
    draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=radius, fill=fill, outline=outline, width=2)
    return layer


def _draw_handle(
    layer: Image.Image,
    body_bounds: tuple[int, int, int, int],
    calibration,
    diagnostics: ProjectionDiagnostics,
    *,
    engineering: bool,
) -> None:
    left, top, right, bottom = body_bounds
    body_width = right - left
    body_height = bottom - top
    delta = diagnostics.handle_delta_degrees
    side = 1 if delta <= 0 else -1
    visibility = max(0.08, 1.0 - abs(abs(delta) - 90.0) / 100.0)
    handle_width = max(18, round(body_width * calibration.handle_width_fraction * visibility))
    handle_height = max(45, round(body_height * calibration.handle_height_fraction))
    stroke = max(8, round(body_height * calibration.handle_stroke_fraction))
    cy = (top + bottom) // 2
    if side > 0:
        box = (right - stroke // 2, cy - handle_height // 2, right + handle_width, cy + handle_height // 2)
    else:
        box = (left - handle_width, cy - handle_height // 2, left + stroke // 2, cy + handle_height // 2)
    draw = ImageDraw.Draw(layer)
    colour = (238, 238, 235, 255) if not engineering else (220, 220, 220, 255)
    outline = (210, 210, 207, 255) if not engineering else (155, 155, 155, 255)
    draw.ellipse(box, fill=colour, outline=outline, width=max(1, stroke // 5))
    inner_margin = stroke
    inner = (
        box[0] + inner_margin, box[1] + inner_margin,
        box[2] - inner_margin, box[3] - inner_margin,
    )
    if inner[2] > inner[0] and inner[3] > inner[1]:
        draw.ellipse(inner, fill=(0, 0, 0, 0))


def _draw_customer_environment(
    image: Image.Image,
    body_bounds: tuple[int, int, int, int],
) -> None:
    left, top, right, bottom = body_bounds
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    pad_x = max(20, (right - left) // 4)
    draw.ellipse(
        (left - pad_x, bottom - 18, right + pad_x, bottom + 45),
        fill=(40, 35, 30, 52),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))
    image.alpha_composite(shadow)


def _draw_engineering_environment(
    image: Image.Image,
    body_bounds: tuple[int, int, int, int],
) -> None:
    draw = ImageDraw.Draw(image)
    spacing = 50
    for x in range(0, image.width, spacing):
        draw.line((x, 0, x, image.height), fill=(225, 225, 225, 255), width=1)
    for y in range(0, image.height, spacing):
        draw.line((0, y, image.width, y), fill=(225, 225, 225, 255), width=1)


def _apply_surface_lighting(
    mug_layer: Image.Image,
    body_bounds: tuple[int, int, int, int],
    body_mask: Image.Image,
) -> Image.Image:
    left, top, right, bottom = body_bounds
    width = right - left
    lighting = Image.new("L", mug_layer.size, 255)
    pixels = lighting.load()
    for x in range(left, right):
        normal = (x - (left + right - 1) / 2) / max(width / 2, 1)
        edge = abs(normal)
        shade = 1.0 - 0.19 * edge ** 1.7
        highlight = 0.07 * max(0.0, cos((normal + 0.24) * pi)) ** 7
        value = max(0, min(255, round(255 * (shade + highlight))))
        for y in range(top, bottom):
            if body_mask.getpixel((x, y)):
                pixels[x, y] = value
    rgb = mug_layer.convert("RGB")
    dark = ImageEnhance.Brightness(rgb).enhance(0.72)
    lit = Image.composite(rgb, dark, lighting)
    result = lit.convert("RGBA")
    result.putalpha(mug_layer.getchannel("A"))
    return result


def _draw_rim_highlight(
    image: Image.Image,
    body_bounds: tuple[int, int, int, int],
) -> None:
    left, top, right, _ = body_bounds
    draw = ImageDraw.Draw(image, "RGBA")
    draw.arc((left + 5, top - 7, right - 5, top + 25), 190, 350, fill=(255, 255, 255, 150), width=3)


def _draw_engineering_guides(
    image: Image.Image,
    body_bounds: tuple[int, int, int, int],
    printable_bounds: tuple[int, int, int, int],
    diagnostics: ProjectionDiagnostics,
    wrap_span_degrees: float,
    view: PreviewV2View,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    left, top, right, bottom = body_bounds
    pleft, ptop, pright, pbottom = printable_bounds
    cx = (left + right) // 2
    draw.rectangle((left, top, right - 1, bottom - 1), outline=(70, 70, 70, 210), width=2)
    draw.rectangle((pleft, ptop, pright - 1, pbottom - 1), outline=(30, 125, 220, 220), width=2)
    draw.line((cx, top, cx, bottom), fill=(220, 50, 50, 220), width=2)
    text = (
        f"{view.value.upper()}  camera yaw centre={diagnostics.camera_centre_x:.1f}px  "
        f"handle={diagnostics.handle_delta_degrees:+.1f}°  print arc={wrap_span_degrees:.1f}°"
    )
    draw.rectangle((12, 12, min(image.width - 12, 760), 42), fill=(255, 255, 255, 225))
    draw.text((20, 19), text, fill=(25, 25, 25, 255))

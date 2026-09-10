"""Pixel regressions using the unchanged production Acre Villas context SVG."""
from dataclasses import fields, replace
from pathlib import Path
import gzip
import hashlib
import json
import math
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageFilter
import pytest

from mug_previewer.design import DesignOptions, build_render_options
from mug_previewer.rendering import context_map
from mug_previewer.preprocessed_export import export_preprocessed_provider_png
from test_batch_export import prepared

FIXTURE = Path(__file__).parent / 'fixtures' / 'acre_villas'


@pytest.fixture
def acre(prepared, monkeypatch):
    root, data, _, _ = prepared
    metadata = json.loads((FIXTURE / 'framing.json').read_text())
    payload = gzip.decompress((FIXTURE / 'context.svg.gz').read_bytes())
    assert hashlib.sha256(payload).hexdigest() == metadata['source_sha256']
    path = root / 'context.svg'
    path.write_bytes(payload)
    street = replace(data.streets[0], display_name='Acre Villas', context_path=path,
                     context_source_bounds=None)
    data = replace(data, streets=(street,))
    # Freeze the actual 225-street dataset's P90 input; retain its real framing.
    monkeypatch.setattr(context_map, '_legacy_dataset_reference_span',
                        lambda paths: metadata['dataset_reference_span'])
    def forbidden(*args, **kwargs):
        pytest.fail('Prepared faces must not be regenerated')
    monkeypatch.setattr('mug_previewer.rendering.artwork.render_face', forbidden)
    monkeypatch.setattr('mug_previewer.preprocess.render_face_svg', forbidden)
    return root, data, street


def pink_mask(image, rear_only=False):
    image = image.convert('RGB')
    mask = Image.new('L', image.size)
    mask.putdata([255 if r > 200 and g < 100 and 110 < b < 175 else 0
                  for r, g, b in image.getdata()])
    if rear_only:
        mask.paste(0, (0, 0, image.width // 2, image.height))
    return mask


def centre(mask):
    left, top, right, bottom = mask.getbbox()
    points = [(left + x, top + y) for y in range(bottom - top)
              for x in range(right - left) if mask.getpixel((left + x, top + y))]
    return tuple(sum(p[i] for p in points) / len(points) for i in (0, 1))


def widths(image, origin):
    """Measure one fixed perpendicular cross-section with bilinear sampling.

    Pink threshold includes the antialiased edge. The contiguous outer footprint
    includes pink/white blends and white, stopping where the map resumes.
    """
    image = image.convert('RGB')
    nx, ny = 5.25 / math.hypot(5.25, 4.62), 4.62 / math.hypot(5.25, 4.62)
    pink, outer = [], []
    for index in range(1601):
        t = (index - 800) * 0.05
        x, y = origin[0] + t * nx, origin[1] + t * ny
        ix, iy = math.floor(x), math.floor(y)
        u, v = x - ix, y - iy
        corners = [image.getpixel((ix + dx, iy + dy)) for dx, dy in ((0,0),(1,0),(0,1),(1,1))]
        weights = ((1-u)*(1-v), u*(1-v), (1-u)*v, u*v)
        r, g, b = [sum(p[c] * w for p, w in zip(corners, weights)) for c in range(3)]
        is_pink = r - g > 60 and b - g > 25
        pink.append(is_pink)
        outer.append(is_pink or min(r,g,b) > 245 or
                     (r > 228 and abs((b-140) - (g-62)*115/193) < 10))
    left = right = 800
    while outer[left-1]:
        left -= 1
    while outer[right+1]:
        right += 1
    selected = [i for i in range(left, right+1) if pink[i]]
    return (max(selected)-min(selected)+1)*0.05, (right-left+1)*0.05


def assert_local_pixel_change(normal, thin, *, rear_only=False):
    mask = pink_mask(normal, rear_only)
    c = centre(mask)
    # Thresholded antialiasing changes the centroid by a fraction of a pixel.
    assert math.dist(c, centre(pink_mask(thin, rear_only))) < 0.5
    wide_pink, wide_outer = widths(normal, c)
    thin_pink, thin_outer = widths(thin, c)
    assert 0.70 < thin_pink / wide_pink < 0.82
    assert 0.68 < thin_outer / wide_outer < 0.82
    assert 0.64 < (thin_outer-thin_pink)/(wide_outer-wide_pink) < 0.85
    diff = ImageChops.difference(normal.convert('RGBA'), thin.convert('RGBA'))
    changed = Image.new('L', normal.size)
    for channel in diff.split():
        changed = ImageChops.lighter(changed, channel)
    assert changed.getbbox() is not None
    # Includes halo and antialiasing/filter support; everything else is identical.
    allowed = mask.filter(ImageFilter.MaxFilter(41 if rear_only else 21))
    assert ImageChops.subtract(changed, allowed).getbbox() is None
    return wide_pink, thin_pink, wide_outer, thin_outer


def test_actual_svg_has_direct_unclassed_matching_halo_and_pink_strokes(acre):
    _, _, street = acre
    root = ET.parse(street.context_path).getroot()
    lines = [e for e in root if e.tag.endswith('polyline')]
    assert [e.get('stroke-width') for e in lines] == ['12.00', '8.00']
    assert [e.get('stroke') for e in lines] == ['#ffffff', '#E83E8C']
    assert lines[0].get('points') == lines[1].get('points')
    assert all('class' not in e.attrib and 'style' not in e.attrib for e in lines)
    original = street.context_path.read_text()
    adjusted = context_map._scale_highlight_stroke(original, 0.6375)
    assert adjusted == original.replace('stroke-width="12.00"', 'stroke-width="7.65"').replace('stroke-width="8.00"', 'stroke-width="5.10"')


def test_actual_context_pixels_have_thinner_pink_and_halo_without_moving_map(acre):
    _, data, street = acre
    results = [context_map.render_context_map_result(data, street,
               build_render_options(DesignOptions(rear_highlight_weight=w), area='Hebden Bridge').context_options)
               for w in (1.0, 0.75)]
    for field in fields(results[0]):
        if field.name not in ('image', 'projected_highlight_width_px'):
            assert getattr(results[0], field.name) == getattr(results[1], field.name)
    assert_local_pixel_change(results[0].image, results[1].image)


def test_inkthreadable_pixels_change_only_around_rear_street_and_preserve_svgs(acre):
    root, data, street = acre
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*.svg')}
    images = []
    for weight in (1.0, 0.75):
        path = export_preprocessed_provider_png(root, data, street, root / f'{weight}.png',
                   profile_id='inkthreadable_11oz_white', design_options=DesignOptions(rear_highlight_weight=weight))
        with Image.open(path) as image:
            assert image.size == (2362, 1063)
            images.append(image.copy())
    assert_local_pixel_change(*images, rear_only=True)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*.svg')} == before


def test_unrelated_white_paths_and_different_transforms_are_not_scaled():
    markup = ('<svg><metadata>{"highlight_color":"#E83E8C"}</metadata>'
              '<path d="M1 2 L3 4" stroke="white" stroke-width="12" transform="translate(10)"/>'
              '<path d="M1 2 L3 4" stroke="#E83E8C" stroke-width="8"/>'
              '<path d="M5 6 L7 8" stroke="white" stroke-width="12"/></svg>')
    assert context_map._scale_highlight_stroke(markup, .75) == markup.replace('stroke-width="8"', 'stroke-width="6.00"')

from pathlib import Path
import json

import pytest
from PIL import Image

from mug_previewer import preprocess
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.diagnostics.front_candidates import ProductionTriageStatus as Status
from mug_previewer.preprocessed_export import (
    AuthoritativeArtworkError, render_authoritative_face_panel,
    render_preprocessed_wrap, export_preprocessed_provider_png,
)
from mug_previewer.rendering.svg_raster import rasterize_face_svg
from mug_previewer.review_index import save_review_record

PROFILES = [('inkthreadable_11oz_white', (2362, 1063)),
            ('printify_generic_11oz_ceramic', (2475, 1155))]


def svg(colour):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" '
            f'viewBox="0 0 990 462"><rect width="495" height="462" fill="{colour}"/>'
            '<rect x="495" width="495" height="462" fill="lime"/></svg>')


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    data = load_dataset(Path(__file__).parent / 'fixtures/workflow_v6_valid')
    street = data.streets[0]
    (tmp_path / 'generated.svg').write_text(svg('red'))
    def record(state):
        (tmp_path / preprocess.INDEX_FILENAME).write_text(json.dumps({'records': [{
            'dataset_id': data.id, 'street_id': street.id, 'success': True,
            'production_state': state.value, 'svg_path': 'generated.svg',
        }]}))
    record(Status.MANUAL_REVIEW)
    def forbidden(*args, **kwargs):
        pytest.fail('live face renderer or production scoring must not run')
    for target in ('mug_previewer.rendering.face.render_face',
                   'mug_previewer.rendering.face.render_face_svg',
                   'mug_previewer.rendering.artwork.render_face',
                   'mug_previewer.rendering.artwork.render_wrap',
                   'mug_previewer.rendering.artwork.render_wrap_result',
                   'mug_previewer.ui.state.render_wrap',
                   'mug_previewer.ui.state.render_wrap_result',
                   'mug_previewer.preprocess.render_face_svg',
                   'mug_previewer.preprocess.production_triage_state'):
        monkeypatch.setattr(target, forbidden)
    return tmp_path, data, street, record


@pytest.mark.parametrize('kind', ['text', 'bytes', 'path'])
def test_shared_raster_exact_front_crop(tmp_path, kind):
    source = svg('red')
    if kind == 'bytes':
        source = source.encode()
    elif kind == 'path':
        source = tmp_path / 'face.svg'
        source.write_text(svg('red'))
    panel = rasterize_face_svg(source)
    assert panel.mode == 'RGBA' and panel.size == (495, 462)
    assert panel.getextrema() == ((255,255),(0,0),(0,0),(255,255))


@pytest.mark.parametrize('profile,size', PROFILES)
@pytest.mark.parametrize('manual', [False, True])
def test_approved_artwork_reaches_actual_provider_png(prepared, profile, size, manual):
    root, data, street, record = prepared
    if manual:
        before = render_authoritative_face_panel(root, data, street)
        assert before.production_approved
        edited = root / 'edited.svg'
        edited.write_text(svg('blue'))
        approved = preprocess.approve_manual_svg(data, street, root, edited)
        assert approved.state is Status.MANUAL_APPROVED
        assert approved.path.read_text() == svg('blue')
        colour = (0, 0, 255, 255)
    else:
        record(Status.AUTO_APPROVED)
        colour = (255, 0, 0, 255)
    panel = render_authoritative_face_panel(root, data, street)
    assert panel.production_approved
    assert panel.image.getpixel((247, 231)) == colour
    wrap = render_preprocessed_wrap(root, data, street)
    assert wrap.size == (2362, 1063) and wrap.info['dpi'] == (300, 300)
    assert wrap.getpixel((472, 531)) == colour
    save_review_record(root, data.id, street.id, 'pass', panel.resolution.path)
    destination = root / 'export.png'
    assert export_preprocessed_provider_png(root, data, street, destination, profile_id=profile) == destination
    with Image.open(destination) as exported:
        assert exported.size == size
        assert abs(exported.info['dpi'][0] - 300) < 0.1
        assert exported.convert('RGBA').getpixel((size[0]//5, size[1]//2)) == colour
        if manual:
            assert exported.convert('RGBA').getpixel((size[0]//5, size[1]//2)) != before.image.getpixel((247, 231))



@pytest.mark.parametrize('state,message', [
    (Status.UNRENDERABLE_INPUT, 'no front artwork'),
])
def test_unapproved_export_rejected_before_rear(prepared, monkeypatch, state, message):
    root, data, street, record = prepared
    record(state)
    monkeypatch.setattr('mug_previewer.preprocessed_export.render_context_map_result',
                        lambda *a: pytest.fail('rear must not run'))
    destination = root / 'blocked.png'
    with pytest.raises(AuthoritativeArtworkError, match=message):
        export_preprocessed_provider_png(root, data, street, destination, profile_id=PROFILES[0][0])
    assert not destination.exists()


@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('invalid', [False, True])
def test_missing_or_invalid_authoritative_asset_never_falls_back(prepared, monkeypatch, manual, invalid):
    root, data, street, record = prepared
    if manual:
        edited = root / 'edited.svg'
        edited.write_text(svg('blue'))
        path = preprocess.approve_manual_svg(data, street, root, edited).path
    else:
        record(Status.AUTO_APPROVED)
        path = root / 'generated.svg'
    if invalid:
        path.write_text('<broken')
    else:
        path.unlink()
    monkeypatch.setattr('mug_previewer.preprocessed_export.render_context_map_result',
                        lambda *a: pytest.fail('rear must not run'))
    with pytest.raises(AuthoritativeArtworkError, match='cannot rasterise|asset integrity'):
        export_preprocessed_provider_png(root, data, street, root / 'bad.png', profile_id=PROFILES[0][0])
    assert not (root / 'bad.png').exists()
    if manual:
        assert path.read_text() == svg('blue')  # Last-good bytes restored in place.

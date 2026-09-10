from pathlib import Path
import json

from PIL import Image

from mug_previewer import preprocess
from mug_previewer.canonical_svg import history_paths, migrate_legacy_records
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.manual_svg_workspace import ManualSvgWorkspace
from mug_previewer.ui.state import load_preprocessed_catalogue


FIXTURE = Path(__file__).parent / 'fixtures' / 'workflow_v6_valid'


def canonical_svg(colour):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" viewBox="0 0 990 462">'
        f'<g class="front-composition"><rect width="495" height="462" fill="{colour}"/></g></svg>'
    )


def a3_svg(colour):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'width="297mm" height="420mm" viewBox="0 0 297 420">'
        '<g id="layer1" inkscape:groupmode="layer">'
        '<g class="front-composition" transform="matrix(.312,0,0,.312,71,146)">'
        f'<rect width="495" height="462" fill="{colour}"/>'
        '</g></g></svg>'
    )


def test_prepare_edit_repairs_legacy_a3_page_from_corrected_same_street_reference(tmp_path):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    folder = tmp_path / 'faces' / data.id
    folder.mkdir(parents=True)
    canonical = folder / '0001_st_johns_road.svg'
    canonical.write_text(a3_svg('blue'))
    corrected = folder / 'corrected' / '0001_st_johns_road_edit.svg'
    corrected.parent.mkdir()
    corrected.write_text(canonical_svg('red'))
    preview = tmp_path / 'previews' / data.id / '0001_st_johns_road.png'
    preprocess._write_preview(canonical_svg('red'), preview)
    (tmp_path / preprocess.INDEX_FILENAME).write_text(json.dumps({'records': [{
        'dataset_id': data.id,
        'street_id': street.id,
        'success': True,
        'production_state': 'MANUAL_REVIEW',
        'svg_path': canonical.relative_to(tmp_path).as_posix(),
        'generated_svg_path': canonical.relative_to(tmp_path).as_posix(),
        'preview_path': preview.relative_to(tmp_path).as_posix(),
    }]}))
    legacy_corrected = corrected.read_bytes()

    catalogue = load_preprocessed_catalogue(tmp_path)
    workspace = ManualSvgWorkspace.from_record(catalogue.find(data, street))
    assert workspace.create_or_get_working_edit() == canonical

    preprocess.validate_manual_svg(canonical.read_bytes())
    text = canonical.read_text(encoding='utf-8')
    assert 'width="990"' in text and 'height="462"' in text
    assert 'viewBox="0 0 990 462"' in text
    assert 'fill="blue"' in text
    assert corrected.read_bytes() == legacy_corrected

    original, good = history_paths(canonical)
    assert original.is_file() and good.is_file()
    preprocess.validate_manual_svg(original.read_bytes())
    assert good.read_bytes() == canonical.read_bytes()
    with Image.open(preview) as image:
        assert image.convert('RGBA').getpixel((247, 231)) == (0, 0, 255, 255)


def test_legacy_approved_a3_page_is_normalized_before_becoming_canonical(tmp_path):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    folder = tmp_path / 'faces' / data.id
    folder.mkdir(parents=True)
    canonical = folder / '0001_st_johns_road.svg'
    canonical.write_text(canonical_svg('red'))
    approved = folder / '0001_st_johns_road.approved.svg'
    approved.write_text(a3_svg('blue'))
    approved_before = approved.read_bytes()
    preview = tmp_path / 'previews' / data.id / '0001_st_johns_road.png'
    preprocess._write_preview(canonical_svg('red'), preview)
    (tmp_path / preprocess.INDEX_FILENAME).write_text(json.dumps({'records': [{
        'dataset_id': data.id,
        'street_id': street.id,
        'success': True,
        'production_state': 'MANUAL_APPROVED',
        'svg_path': approved.relative_to(tmp_path).as_posix(),
        'approved_svg_path': approved.relative_to(tmp_path).as_posix(),
        'generated_svg_path': canonical.relative_to(tmp_path).as_posix(),
        'preview_path': preview.relative_to(tmp_path).as_posix(),
    }]}))

    assert migrate_legacy_records(tmp_path) == 1
    preprocess.validate_manual_svg(canonical.read_bytes())
    assert 'fill="blue"' in canonical.read_text(encoding='utf-8')
    assert approved.read_bytes() == approved_before
    original, good = history_paths(canonical)
    assert original.read_bytes() == canonical_svg('red').encode()
    assert good.read_bytes() == canonical.read_bytes()
    record = load_preprocessed_catalogue(tmp_path).find(data, street)
    assert record.svg_path == canonical
    assert record.approved_svg_path is None
    with Image.open(preview) as image:
        assert image.convert('RGBA').getpixel((247, 231)) == (0, 0, 255, 255)


def test_invalid_legacy_page_without_safe_reference_is_left_untouched(tmp_path):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    folder = tmp_path / 'faces' / data.id
    folder.mkdir(parents=True)
    canonical = folder / '0001_st_johns_road.svg'
    canonical.write_text(a3_svg('blue'))
    before = canonical.read_bytes()
    (tmp_path / preprocess.INDEX_FILENAME).write_text(json.dumps({'records': [{
        'dataset_id': data.id,
        'street_id': street.id,
        'success': True,
        'production_state': 'MANUAL_REVIEW',
        'svg_path': canonical.relative_to(tmp_path).as_posix(),
        'generated_svg_path': canonical.relative_to(tmp_path).as_posix(),
    }]}))

    catalogue = load_preprocessed_catalogue(tmp_path)
    workspace = ManualSvgWorkspace.from_record(catalogue.find(data, street))
    try:
        workspace.create_or_get_working_edit()
    except preprocess.SvgApprovalError as error:
        assert '990x462' in str(error)
    else:
        raise AssertionError('Expected invalid page geometry to remain blocked without a safe reference')
    assert canonical.read_bytes() == before
    original, good = history_paths(canonical)
    assert not original.exists() and not good.exists()

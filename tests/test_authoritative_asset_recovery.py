from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from PIL import Image
import pytest

from mug_previewer import preprocess
from mug_previewer.batch_export import build_batch_plan
from mug_previewer.canonical_svg import history_paths
from mug_previewer.datasets.loader import load_dataset
from mug_previewer.preprocessed_export import export_preprocessed_provider_png
from mug_previewer.ui.state import load_preprocessed_catalogue


FIXTURE = Path(__file__).parent / 'fixtures/workflow_v6_valid'


def svg(colour: str) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" '
        f'viewBox="0 0 990 462"><g class="front-composition">'
        f'<rect width="495" height="462" fill="{colour}"/></g></svg>'
    ).encode()


def slug(value: str) -> str:
    return ''.join(char.lower() if char.isalnum() else '_' for char in value).strip('_') or 'street'


def prepared_path(root: Path, dataset_id: str, street_id: str, name: str) -> Path:
    return root / 'faces' / slug(dataset_id) / f'{street_id}_{slug(name)}.svg'


def preview_path(root: Path, dataset_id: str, street_id: str, name: str) -> Path:
    return root / 'previews' / slug(dataset_id) / f'{street_id}_{slug(name)}.png'


def write_index(root: Path, records: list[dict]) -> None:
    (root / preprocess.INDEX_FILENAME).write_text(
        json.dumps({'format': 'mug-previewer-preprocess-index-v1', 'records': records}),
        encoding='utf-8',
    )


def record_for(root: Path, data, street, *, state='MANUAL_REVIEW', path=None, preview=None):
    path = path or prepared_path(root, data.id, street.id, street.display_name)
    return {
        'dataset_id': data.id,
        'dataset_name': data.display_name,
        'street_id': street.id,
        'street_name': street.display_name,
        'production_state': state,
        'success': True,
        'svg_path': path.relative_to(root).as_posix() if path is not None else None,
        'preview_path': preview.relative_to(root).as_posix() if preview is not None else None,
    }


def write_history(canonical: Path, *, generated=b'', last_good=b'') -> None:
    generated_path, good_path = history_paths(canonical)
    if generated:
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_bytes(generated)
    if last_good:
        good_path.parent.mkdir(parents=True, exist_ok=True)
        good_path.write_bytes(last_good)


def test_missing_manual_canonical_is_restored_from_last_good_not_generated(tmp_path, monkeypatch):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    canonical = prepared_path(tmp_path, data.id, street.id, street.display_name)
    original, edited = svg('red'), svg('blue')
    write_history(canonical, generated=original, last_good=edited)
    write_index(tmp_path, [record_for(tmp_path, data, street, state='MANUAL_APPROVED', path=canonical)])

    monkeypatch.setattr(preprocess, 'render_face_svg', lambda *a, **k: pytest.fail('recovery must not regenerate faces'))
    catalogue = load_preprocessed_catalogue(tmp_path)
    recovered = catalogue.find(data, street)

    assert recovered.svg_path == canonical
    assert canonical.read_bytes() == edited
    assert canonical.read_bytes() != original
    assert recovered.generated_svg_path == history_paths(canonical)[0]
    saved = json.loads((tmp_path / preprocess.INDEX_FILENAME).read_text(encoding='utf-8'))['records'][0]
    assert saved['svg_path'] == canonical.relative_to(tmp_path).as_posix()
    assert 'approved_svg_path' not in saved


def test_stale_index_repoints_to_existing_face_without_rewriting_it(tmp_path):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    canonical = prepared_path(tmp_path, data.id, street.id, street.display_name)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(svg('blue'))
    before = (canonical.read_bytes(), canonical.stat().st_mtime_ns)
    preview = preview_path(tmp_path, data.id, street.id, street.display_name)
    preview.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (8, 8), 'white').save(preview)
    stale = canonical.with_name('missing_old_name.svg')
    write_index(tmp_path, [record_for(tmp_path, data, street, path=stale, preview=preview)])

    catalogue = load_preprocessed_catalogue(tmp_path)
    recovered = catalogue.find(data, street)

    assert recovered.svg_path == canonical
    assert (canonical.read_bytes(), canonical.stat().st_mtime_ns) == before
    saved = json.loads((tmp_path / preprocess.INDEX_FILENAME).read_text(encoding='utf-8'))['records'][0]
    assert saved['svg_path'] == canonical.relative_to(tmp_path).as_posix()


def test_unambiguous_same_street_face_recovers_but_ambiguous_faces_do_not(tmp_path):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    folder = tmp_path / 'faces' / slug(data.id)
    folder.mkdir(parents=True)
    first = folder / f'{street.id}_renamed_street.svg'
    first.write_bytes(svg('blue'))
    stale = folder / 'missing.svg'
    write_index(tmp_path, [record_for(tmp_path, data, street, path=stale)])

    assert load_preprocessed_catalogue(tmp_path).find(data, street).svg_path == first

    second = folder / f'{street.id}_second_name.svg'
    second.write_bytes(svg('red'))
    stale.unlink(missing_ok=True)
    write_index(tmp_path, [record_for(tmp_path, data, street, path=stale)])
    record = load_preprocessed_catalogue(tmp_path).find(data, street)
    assert record.svg_path == stale
    assert not record.svg_path.exists()


def test_batch_plan_recovers_prepared_set_instead_of_mass_asset_errors(tmp_path):
    original = load_dataset(FIXTURE)
    streets = tuple(
        replace(original.streets[0], id=f'{index:04}', display_name=f'Street {index}')
        for index in range(1, 6)
    )
    data = replace(original, streets=streets)
    records = []
    for street in streets[:-1]:
        canonical = prepared_path(tmp_path, data.id, street.id, street.display_name)
        write_history(canonical, generated=svg('red'), last_good=svg('blue'))
        records.append(record_for(tmp_path, data, street, path=canonical))
    records.append({
        'dataset_id': data.id,
        'dataset_name': data.display_name,
        'street_id': streets[-1].id,
        'street_name': streets[-1].display_name,
        'production_state': 'UNRENDERABLE_INPUT',
        'success': True,
        'svg_path': None,
        'preview_path': None,
    })
    write_index(tmp_path, records)

    plan = build_batch_plan(tmp_path, data, 'inkthreadable_11oz_white', tmp_path / 'out')

    assert plan.summary.total == 5
    assert plan.summary.ready == 4
    assert plan.summary.unrenderable == 1
    assert plan.summary.asset_errors == 0
    assert all(item.authoritative_svg and item.authoritative_svg.is_file()
               for item in plan.items if item.eligibility.value == 'READY')


def test_catalogue_restart_keeps_recovered_authority_and_does_not_rewrite(tmp_path):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    canonical = prepared_path(tmp_path, data.id, street.id, street.display_name)
    write_history(canonical, generated=svg('red'), last_good=svg('blue'))
    write_index(tmp_path, [record_for(tmp_path, data, street, state='MANUAL_APPROVED', path=canonical)])

    first = load_preprocessed_catalogue(tmp_path).find(data, street)
    before = (canonical.read_bytes(), canonical.stat().st_mtime_ns)
    second = load_preprocessed_catalogue(tmp_path).find(data, street)

    assert first.svg_path == second.svg_path == canonical
    assert (canonical.read_bytes(), canonical.stat().st_mtime_ns) == before


def test_genuine_missing_face_stays_blocked_even_when_cached_preview_exists(tmp_path):
    from test_preprocessed_ui import _controller

    data = load_dataset(FIXTURE)
    street = data.streets[0]
    canonical = prepared_path(tmp_path, data.id, street.id, street.display_name)
    preview = preview_path(tmp_path, data.id, street.id, street.display_name)
    preview.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (24, 18), 'white').save(preview)
    write_index(tmp_path, [record_for(tmp_path, data, street, path=canonical, preview=preview)])
    catalogue = load_preprocessed_catalogue(tmp_path)

    app = _controller(catalogue, street)
    app.state.selected_dataset = data
    app._select_street()

    assert app.state.current_front_preview is not None
    assert app.export_button.state == app.printify_export_button.state == 'disabled'
    assert not canonical.exists()


def test_recovered_face_enables_single_export_ui_and_editing(tmp_path):
    from test_preprocessed_ui import _controller, _Var
    from mug_previewer.manual_svg_workspace import ManualSvgWorkspace

    data = load_dataset(FIXTURE)
    street = data.streets[0]
    canonical = prepared_path(tmp_path, data.id, street.id, street.display_name)
    preview = preview_path(tmp_path, data.id, street.id, street.display_name)
    preview.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (24, 18), 'white').save(preview)
    write_history(canonical, generated=svg('red'), last_good=svg('blue'))
    write_index(tmp_path, [record_for(tmp_path, data, street, path=canonical, preview=preview)])
    catalogue = load_preprocessed_catalogue(tmp_path)

    app = _controller(catalogue, street)
    app.state.selected_dataset = data
    app.current_face_var = _Var()
    app._select_street()
    app._refresh_current_face_label()
    record = catalogue.find(data, street)

    assert app.export_button.state == app.printify_export_button.state == 'normal'
    assert 'Face unavailable' not in app.current_face_var.value
    assert ManualSvgWorkspace.from_record(record).can_edit


def test_single_export_after_recovery_preserves_recovered_svg(tmp_path):
    data = load_dataset(FIXTURE)
    street = data.streets[0]
    canonical = prepared_path(tmp_path, data.id, street.id, street.display_name)
    edited = svg('blue')
    write_history(canonical, generated=svg('red'), last_good=edited)
    write_index(tmp_path, [record_for(tmp_path, data, street, state='MANUAL_APPROVED', path=canonical)])
    load_preprocessed_catalogue(tmp_path)
    before = (canonical.read_bytes(), canonical.stat().st_mtime_ns)

    destination = tmp_path / 'single.png'
    export_preprocessed_provider_png(
        tmp_path, data, street, destination, profile_id='inkthreadable_11oz_white'
    )

    assert destination.is_file()
    assert (canonical.read_bytes(), canonical.stat().st_mtime_ns) == before
    with Image.open(destination) as exported:
        assert exported.size == (2362, 1063)

from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from mug_previewer.svg_edit_repair import corrected_svg, correct_edited_svg, SVG

ORIGINAL = '''<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" viewBox="0 0 990 462">
<metadata id="mug-previewer-metadata">{"street_id":"0006"}</metadata>
<g class="front-composition" transform="translate(0 60) scale(1.18)"><path d="M0 0L10 10"/></g></svg>'''
EDITED = '''<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" width="297mm" height="420mm" viewBox="0 0 297 420">
<sodipodi:namedview id="view"/>
<defs><style>.ink {stroke:red}</style></defs>
<g id="layer1" inkscape:groupmode="layer"><g class="front-composition" transform="matrix(.312,0,0,.312,71,146)">
<path id="edited-feature" class="ink" d="M1 2L30 40" transform="rotate(-90)" inkscape:label="Feature"/>
<text x="20">Edited title</text></g></g></svg>'''


def test_restores_page_and_preserves_edits():
    result = ET.fromstring(corrected_svg(ORIGINAL, EDITED))
    assert [result.get(k) for k in ('width', 'height', 'viewBox')] == ['990', '462', '0 0 990 462']
    group = result.find(f'.//{SVG}g[@class="front-composition"]')
    assert group.get('transform') == 'translate(0 60) scale(1.18)'
    path = group.find(f'{SVG}path')
    assert path.get('d') == 'M1 2L30 40'
    assert path.get('transform') == 'rotate(-90)'
    assert group.find(f'{SVG}text').text == 'Edited title'
    assert result.find(f'{SVG}defs/{SVG}style').text == '.ink {stroke:red}'
    assert result.find(f'{SVG}metadata').text == '{"street_id":"0006"}'
    text = ET.tostring(result, encoding='unicode')
    assert 'inkscape' not in text and 'sodipodi' not in text
    assert corrected_svg(ORIGINAL, corrected_svg(ORIGINAL, EDITED)) == corrected_svg(ORIGINAL, EDITED)


@pytest.mark.parametrize('edited', [
    EDITED.replace('front-composition', 'unknown'),
    EDITED.replace('id="layer1"', 'id="layer1" transform="scale(2)"'),
    EDITED.replace('id="layer1"', 'id="layer1" style="opacity:.5"'),
    '<not-svg/>',
])
def test_rejects_unsupported_layout(edited):
    with pytest.raises(ValueError):
        corrected_svg(ORIGINAL, edited)


def test_never_overwrites_files(tmp_path):
    original, edited, output = [tmp_path / name for name in ('original.svg', 'edited.svg', 'corrected.svg')]
    original.write_text(ORIGINAL)
    edited.write_text(EDITED)
    with pytest.raises(ValueError):
        correct_edited_svg(original, edited, edited)
    correct_edited_svg(original, edited, output)
    with pytest.raises(FileExistsError):
        correct_edited_svg(original, edited, output)
    assert original.read_text() == ORIGINAL
    assert edited.read_text() == EDITED

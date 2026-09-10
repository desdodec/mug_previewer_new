import xml.etree.ElementTree as ET

import pytest

from mug_previewer.svg_edit_repair import SVG, corrected_svg


REFERENCE = '''<svg xmlns="http://www.w3.org/2000/svg" width="990" height="462" viewBox="0 0 990 462">
<g id="layer1"><g class="front-composition" transform="translate(0 60) scale(1.18)"><path d="M0 0L10 10"/></g></g></svg>'''

EDITED = '''<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="297mm" height="420mm" viewBox="0 0 297 420">
<g id="layer1" inkscape:groupmode="layer"><g class="front-composition" transform="matrix(.312,0,0,.312,71,146)"><path id="edited" d="M1 2L30 40"/></g></g></svg>'''


def test_plain_wrapper_around_reference_composition_is_safe():
    result = ET.fromstring(corrected_svg(REFERENCE, EDITED))
    assert [result.get(key) for key in ('width', 'height', 'viewBox')] == [
        '990', '462', '0 0 990 462'
    ]
    group = next(
        node for node in result.iter()
        if 'front-composition' in node.get('class', '').split()
    )
    assert group.tag == SVG + 'g'
    assert group.get('transform') == 'translate(0 60) scale(1.18)'
    assert group.find(SVG + 'path').get('d') == 'M1 2L30 40'


@pytest.mark.parametrize('attribute', ['transform="scale(2)"', 'style="opacity:.5"'])
def test_reference_wrapper_that_changes_placement_is_still_rejected(attribute):
    unsafe = REFERENCE.replace('<g id="layer1">', f'<g id="layer1" {attribute}>')
    with pytest.raises(ValueError, match='Unsupported original artwork wrapper'):
        corrected_svg(unsafe, EDITED)

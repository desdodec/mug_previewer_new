"""Restore original page layout while preserving edits inside SVG artwork.

Usage: python -m mug_previewer.svg_edit_repair PATH_TO_FACE_FOLDER
Writes separate copies into a corrected subfolder; never overwrites files.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

SVG = "{http://www.w3.org/2000/svg}"
EDITOR_NAMESPACES = (
    "{http://www.inkscape.org/namespaces/inkscape}",
    "{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}",
)


def _composition(root: ET.Element) -> ET.Element:
    matches = [node for node in root.iter()
               if "front-composition" in node.get("class", "").split()]
    if len(matches) != 1 or matches[0].tag != SVG + "g":
        raise ValueError("Expected exactly one front-composition artwork group")
    return matches[0]


def corrected_svg(original: str, edited: str) -> str:
    """Restore canvas and outer placement, retaining all inner artwork edits.

    Requires canonical face SVGs with a front-composition group. Only that
    group's outer transform is reset. Unsupported layouts raise ValueError.
    """
    reference = ET.fromstring(original)
    result = ET.fromstring(edited)
    if reference.tag != SVG + "svg" or result.tag != SVG + "svg":
        raise ValueError("Both inputs must be SVG documents")
    source_group = _composition(reference)
    edited_group = _composition(result)
    if source_group not in list(reference):
        raise ValueError("Original artwork must be directly inside the SVG page")
    parents = {child: parent for parent in result.iter() for child in parent}
    ancestor = parents[edited_group]
    while ancestor is not result:
        # Inkscape's plain layer wrapper is harmless. Presentation attributes
        # or additional transforms would require a more general conversion.
        if ancestor.tag != SVG + "g" or any(
            key not in ("id", "class") and not key.startswith(EDITOR_NAMESPACES)
            for key in ancestor.attrib
        ):
            raise ValueError("Unsupported artwork wrapper; cannot safely restore placement")
        ancestor = parents[ancestor]
    for root in (reference, result):
        if "transform" in root.attrib or "style" in root.attrib:
            raise ValueError("Unsupported page transform or style")
    for key in ("width", "height", "viewBox"):
        if key not in reference.attrib:
            raise ValueError(f"Original SVG is missing {key}")
        result.set(key, reference.attrib[key])
    result.attrib.pop("preserveAspectRatio", None)
    if "preserveAspectRatio" in reference.attrib:
        result.set("preserveAspectRatio", reference.attrib["preserveAspectRatio"])
    edited_group.attrib.pop("transform", None)
    if "transform" in source_group.attrib:
        edited_group.set("transform", source_group.attrib["transform"])
    for node in list(result.iter()):
        for child in list(node):
            if child.tag.startswith(EDITOR_NAMESPACES) or child.get("id") == "mug-previewer-metadata":
                node.remove(child)
        for key in list(node.attrib):
            if key.startswith(EDITOR_NAMESPACES):
                del node.attrib[key]
    metadata = reference.find(f"{SVG}metadata[@id='mug-previewer-metadata']")
    if metadata is not None:
        result.insert(0, deepcopy(metadata))
    ET.register_namespace("", SVG[1:-1])
    return ET.tostring(result, encoding="unicode", xml_declaration=True)


def correct_edited_svg(original: Path, edited: Path, output: Path, *, replace: bool = False) -> Path:
    """Save a separate correction; replacement is explicit and never touches inputs."""
    original, edited, output = Path(original), Path(edited), Path(output)
    if output.resolve() in (original.resolve(), edited.resolve()):
        raise ValueError("Output must be a separate file")
    content = corrected_svg(original.read_text(encoding="utf-8-sig"),
                            edited.read_text(encoding="utf-8-sig"))
    output.parent.mkdir(parents=True, exist_ok=True)
    if replace:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                             prefix=".repair-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.replace(temporary, output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    else:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(content)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error("Folder does not exist")
    files = sorted(args.folder.glob("*_edit.svg"))
    finished = 0
    for edited in files:
        original = edited.with_name(edited.name.removesuffix("_edit.svg") + ".svg")
        try:
            output = correct_edited_svg(original, edited, args.folder / "corrected" / edited.name)
        except (OSError, ValueError, ET.ParseError) as error:
            print(f"SKIPPED {edited.name}: {error}")
        else:
            finished += 1
            print(f"Corrected {output.name}")
    print(f"Processed: {len(files)}; corrected: {finished}; skipped: {len(files) - finished}")
    return int(finished != len(files))


if __name__ == "__main__":
    raise SystemExit(main())

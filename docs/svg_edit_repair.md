# Correcting SVGs after editing in Inkscape

The helper restores the original page dimensions and overall artwork placement,
while retaining edits to the shapes, text, styling, and individual feature positions.
It removes Inkscape page/editor settings and restores the original app metadata.

From the project folder, run:

```powershell
.venv\Scripts\python.exe -m mug_previewer.svg_edit_repair "svg_previews\faces\20260905_150413_hebden_bridge_streets_parks_water_boundary_clip"
```

Each `NAME_edit.svg` needs a matching `NAME.svg` original in the same folder.
Corrected files go into that folder's `corrected` subfolder. Originals and edited
inputs are preserved. Existing corrected files are skipped with a reason rather
than overwritten; move previous outputs elsewhere before generating replacements.
The command reports the exact processed, corrected, and skipped counts.

To view the results, reopen `tools/svg_reviewer/previewer.html` and select only the
`corrected` folder using its SVG directory selector. A saved review referring to
original filenames will not automatically include these `_edit.svg` copies.

For use from Python:

```python
from pathlib import Path
from mug_previewer.svg_edit_repair import correct_edited_svg

correct_edited_svg(
    Path("original.svg"),
    Path("original_edit.svg"),
    Path("corrected/original_edit.svg"),
)
```

This helper is intended for this app's canonical face SVGs retaining their
`front-composition` group. It resets that group's outer transform to the original;
all transforms inside it remain edited. It does not crop to the drawing bounds or
recenter individual features. Unsupported wrappers or missing composition groups
are reported rather than guessed. It is not a general-purpose repair for arbitrary
SVGs, flattened drawings, or artwork placed outside the composition group.

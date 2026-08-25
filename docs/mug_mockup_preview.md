# Mug mockup preview

`mug_previewer.preview.render_mug_preview(wrap, options=None)` creates a
deterministic photographic-style review image from the canonical flat master.
It is a downstream sibling of provider export: it accepts only the canonical
`2362 x 1063` wrap and has no dataset, street, or provider dependency.

## Important boundary

The preview applies a cylindrical projection, body mask, lighting, and the
project-owned white-mug photograph. **Mockup output must never be sent to a
print provider.** Manufacture uses the flat provider export, not this image.

## Orientations and output

`front-handle-right` is the default orientation. It centres the canonical front zone; the left cylinder edge can show the rear-side tail and the right edge reaches the seam beside the handle.

`rear-handle-left` centres the canonical rear/context-map zone. Its left edge reaches the seam beside the handle and the right edge can show the front-side tail. The renderer mirrors only the owned blank mug photograph, mask, and geometry; it never mirrors artwork, so text, map labels, and attribution remain readable.

Both orientations use the same cylindrical projection, masking, and lighting. The mask keeps artwork off the handle and background. Output is RGBA `1024 x 1536`, inherited from the owned neutral white-mug studio asset.

```python
from mug_previewer.preview import MugPreviewOptions, render_mug_preview
from mug_previewer.rendering.artwork import render_wrap

wrap = render_wrap(dataset, street)
preview = render_mug_preview(wrap)
preview.save("output/mug_preview.png")

rear_preview = render_mug_preview(
    wrap,
    MugPreviewOptions(orientation="rear-handle-left"),
)
rear_preview.save("output/mug_preview_rear.png")
```

`create_mockup_diagnostic_wrap()` in `mug_previewer.preview.diagnostic`
creates a development-only colour-separated FRONT / SEAM / REAR master for
projection review. Do not commit generated diagnostic or real-preview PNGs.

## Limitations

This is one fixed 2D photographic mockup, not a physical 3D renderer. It has
no interactive rotation, alternative mug models, user photos, colour profiles,
or provider/manufacturing role. It is intended for visual assessment of the
current flat artwork, not for changing that artwork automatically.
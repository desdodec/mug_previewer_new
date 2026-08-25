# Mug mockup preview

`mug_previewer.preview.render_mug_preview(wrap, options=None)` creates a
deterministic photographic-style review image from the canonical flat master.
It is a downstream sibling of provider export: it accepts only the canonical
`2362 x 1063` wrap and has no dataset, street, or provider dependency.

## Important boundary

The preview applies a sinusoidal cylindrical projection, body mask, and the
project-owned white-mug photograph. Transparent source pixels leave the
underlying ceramic unchanged; preview lighting is not applied as an opaque
rectangular print field. **Mockup output must never be sent to a print
provider.** Provider export and mockup preview are separate downstream
consumers of the canonical wrap: manufacture uses the flat provider export,
not this image.

## Orientations and output

`front-handle-right` is the default orientation. It centres the canonical front zone; the left cylinder edge can show the rear-side tail and the right edge reaches the seam beside the handle.

`rear-handle-left` centres the canonical rear/context-map zone. Its left edge reaches the seam beside the handle and the right edge can show the front-side tail. The renderer mirrors only the owned blank mug photograph, mask, and geometry; it never mirrors artwork, so text, map labels, and attribution remain readable.

Both orientations use the same cylindrical projection, masking, and lighting. The mask keeps artwork off the handle and background. Output is RGBA `1024 x 1536`, inherited from the owned neutral white-mug studio asset.

## Rear-panel composition

The rear context map keeps its existing geographic crop and metric-framing
policy; only its physical map-and-attribution group is larger inside the
existing rear panel. The map height is `0.84` of the context-panel height
(`1.20` times the previous `0.70`), remains horizontally centred, and keeps its
aspect ratio. Attribution remains directly below the map with unchanged required
text, a `13 px` font, `16.5 px` line height, and an `11 px` gap so it is legible
but visually tertiary.

The final production rule is rear physical scale `1.20`, which preserves that geographic framing while enlarging only physical presentation. It remains uniformly scaled and centred. The required attribution content is unchanged; its final restrained treatment is `12 px` type with the existing `16.5 px` line height and `11 px` gap. This supersedes the preliminary `13 px` attribution wording above.

The distinction is intentional: do not retune geographic framing when maintaining rear physical scale or attribution placement.

## Task 02L composition calibration

The front title, locality, and face are one physical composition: production applies a uniform `1.08` scale around the front-panel centre plus a downward offset of `6%` of panel height. This improves use of the lower ceramic while preserving the original internal face and type relationships, horizontal centring, front zone, seam exclusion, and canonical canvas.

Rear physical scale remains `1.20`; its map crop, metric framing, map-and-attribution occupied bounds, colour palette, and highlighted-street path geometry are unchanged. Only the highlighted-street stroke is reduced to `0.85` of its incoming width, retaining visible but more restrained road emphasis.

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
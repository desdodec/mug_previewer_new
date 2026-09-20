# Provider exports V3

Mug Previewer V3 keeps artwork generation supplier-independent.

The production renderer produces two complete RGBA groups:

- **frontArtwork** — title, locality/subtitle and face illustration.
- **rearArtwork** — context map and OpenStreetMap attribution.

Supplier profiles do not regenerate those components. The V3 compositor trims
transparent padding from each complete group, preserves its aspect ratio, and
places the visual bounds on normalised supplier anchors.

## Coordinate system

The full supplier wrap uses normalised horizontal coordinates:

- `0.00` — left edge.
- `0.25` — centre of the first mug face.
- `0.50` — point opposite the handle.
- `0.75` — centre of the second mug face.
- `1.00` — right edge.

Both built-in V3 profiles initially use exact quarter-wrap placement. No
perspective correction from angled mockup screenshots is baked into production
artwork.

## Built-in V3 profiles

### Inkthreadable — 11oz White Mug

- Canvas: **2550 x 1125 px**.
- DPI metadata: **300**.
- Front centre: `(0.25, 0.50)` -> **(638, 563)** using half-up rounding.
- Rear centre: `(0.75, 0.50)` -> **(1913, 563)**.
- Front/rear scale: `1.0`.
- Inward correction: `0.0 mm`.
- Transparent PNG background.
- End safe zone: 5% of wrap width at each edge.

### Prodigi — H-MUG-W 11oz White Ceramic Photo Mug

- Published print area: **229 x 95 mm**.
- Working canvas at 300 DPI: **2705 x 1122 px**.
- Front centre: `(0.25, 0.50)`.
- Rear centre: `(0.75, 0.50)`.
- Front/rear scale: `1.0`.
- Inward correction: `0.0 mm`.
- Transparent PNG background.
- End safe zone: 5% of wrap width at each edge.

The Prodigi profile is SKU-specific. Do not reuse these dimensions for another
Prodigi mug unless that product has the same print area.

For H-MUG-W, the production geometry is deliberately conservative: the front
and rear visual centres remain exactly half a wrap apart, both stay vertically
centred, and no horizontal correction is inferred from Prodigi's angled 3D
mockups. The mockup is a preview of a curved object rather than a measurement
surface.

The previous 2048 x 849 artwork aspect ratio was already essentially the same
as the 229 x 95 mm print area. V3 therefore preserves the established group
layout but renders the supplier file directly at the 300-DPI working canvas
instead of moving either artwork group.

Keep comfortable visual breathing room from the wrap ends/handle region.
Sublimation registration should not be treated as millimetre-perfect. If a
physical sample later shows a repeatable registration error, record that as a
supplier-profile calibration (for example `inward_offset_mm`) rather than
changing the shared front or rear artwork.

## Visual bounds

Placement uses the alpha bounds of the complete group rather than its source
container. Transparent padding therefore cannot move the apparent centre.

The front title, locality and face are centred as one visual object. The rear
map and attribution are likewise centred as one visual object.

## Scaling

The established production artwork scale is preserved at `scale=1.0`.
Supplier profile scales are uniform: X and Y always receive the same factor.
A supplier canvas with a different aspect ratio changes only the empty space
around the artwork.

## Physical calibration

Each profile has an `inward_offset_mm` value. It is converted using that
profile's DPI:

`offset_px = inward_offset_mm * dpi / 25.4`

The correction is symmetric:

`front_x = width * front_centre_x + offset_px`

`rear_x = width * rear_centre_x - offset_px`

A later physical print test can therefore move both faces inward without
changing the source artwork or rendering algorithms.

## Debug output

Debug output is always a separate file. It overlays:

- 25%, 50% and 75% vertical guides;
- horizontal centre;
- tight front/rear artwork bounds;
- front/rear centre points;
- supplier end safe zones.

The production image is composed before the debug overlay is created, so guide
pixels cannot enter the print file.

## API

```python
from mug_previewer.exporting import save_profile_set

outputs = save_profile_set(
    front_artwork,
    rear_artwork,
    "output",
    "Aspinall Street",
    debug=True,
)
```

This writes:

```text
aspinall-street_inkthreadable.png
aspinall-street_prodigi.png
aspinall-street_inkthreadable_debug.png
aspinall-street_prodigi_debug.png
```

For prepared/reviewed production artwork,
`export_preprocessed_provider_set(...)` performs the same multi-supplier
export after one front/rear render and re-checks the artwork/QA state before
publishing the staged files.


## Desktop UI

Prepared-workspace mode now separates production export configuration from
screen-only mug preview geometry.

The left-hand **Production print profile (V3)** control selects Inkthreadable
or Prodigi H-MUG-W and shows the exact output size, 300 DPI metadata,
quarter-wrap anchors and current inward correction. The optional debug checkbox
writes a separate `*_debug.png` alongside the production file.

The **Mug preview geometry (screen only)** section is automatically matched to
the selected supplier profile. Camera yaw and preview projection controls do
not alter the production PNG.

The Export PNG tab's batch-provider selector is synchronised with the V3
production profile. Selected-face export uses the same active profile rather
than presenting separate hard-coded supplier buttons.

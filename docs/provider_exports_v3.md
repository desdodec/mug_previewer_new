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

# Mug Previewer V2

Mug Previewer V2 is a preview-only rendering layer built on top of the existing canonical production wrap.
It does not replace prepared SVGs, QA, provider profiles, or provider export.

## Why V2 exists

Typical print-on-demand mug mockups make it difficult to tell whether apparent left/right spacing is caused by:

- artwork placement in the flat print file;
- cylindrical projection around the mug;
- the unprinted handle gap;
- camera rotation / perspective;
- or the provider mockup itself.

V2 separates those effects.

## Core model

The existing canonical wrap remains unchanged at `2362 x 1063`.

V2 maps that flat canvas onto a calibrated physical cylinder. The starting generic 11 oz calibration keeps the measured central seam/handle exclusion already present in the canonical artwork and adds only the outside-canvas arc needed to close the 360-degree mug:

- a 300 degree canonical print-canvas arc;
- the existing central 472 px canonical handle/seam exclusion zone;
- a further 60 degree opposite-side arc outside the canonical print canvas;
- front and rear canonical centres exactly 180 degrees apart on the virtual mug;
- independent camera yaw;
- separate customer and engineering presentation modes.

The 300/60 split is a starting visual calibration, not a provider production specification. Provider/SKU-specific mug calibrations can replace it later without altering the production artwork.

## Views

### V2 Customer Front / Rear

These are presentation views. They add a procedural white ceramic body, handle, surface shading and shadow while retaining the physical cylindrical projection.

### V2 Engineering Rear

This is the geometry-check view. It deliberately includes:

- a neutral grid;
- the mug body boundary;
- the printable-height boundary;
- the optical/camera centre line;
- camera-yaw diagnostics;
- the physical handle direction.

At `0°` camera yaw, use the engineering view to judge true centring. If margins look unequal only after adding camera yaw, the asymmetry is a viewing effect rather than a change to the source artwork.

## Camera yaw

The prepared-artwork workspace exposes `V2 preview camera yaw` from `-30°` to `+30°`.

Changing yaw:

- does not rerender the face;
- does not rerender the context map;
- does not alter the canonical wrap;
- does not alter provider exports;
- only changes how the virtual mug is viewed.

This is specifically intended for comparing square-on engineering geometry with realistic three-quarter mockups.

## Architecture

V2 lives in:

```text
src/mug_previewer/preview_v2/
    models.py
    projection.py
    renderer.py
    calibration_registry.py
    calibrations/
        generic_11oz.json
        inkthreadable_11oz_white.json
        printify_generic_11oz_ceramic.json
        prodigi_h_mug_w.json
```

`models.py` owns calibrated mug geometry and camera/scene options.

`projection.py` expands the canonical wrap into a full 360-degree circumference strip, preserving the canonical central handle/seam zone and adding the outside-canvas arc needed to close the physical cylinder.

`renderer.py` owns preview-only mug presentation and engineering guides.

`calibration_registry.py` loads packaged JSON calibration profiles, derives geometry from physical dimensions when enough evidence exists, and resolves the most specific provider/SKU match. Unknown products fall back explicitly to `generic_11oz_v2`.

## Provider / SKU calibration profiles

The prepared workspace exposes a **V2 mug calibration** selector. Changing it only rerenders V2 previews; it does not alter the canonical wrap or provider export.

Current packaged profiles:

- `generic_11oz_v2` — generic fallback geometry;
- `inkthreadable_11oz_white_v2` — derived from provider-published 97 × 82 mm body dimensions and 200 × 90 mm print area;
- `printify_generic_11oz_ceramic_v2` — estimated from Printify's public 11oz size guide plus the existing generic 2475 × 1155 / 300 DPI Mug Previewer profile;
- `prodigi_h_mug_w_v2` — provisional H-MUG-W profile using Prodigi's published 229 × 95 mm print size and temporary generic 11oz body dimensions until Prodigi body diameter/height are measured or confirmed.

Calibration status is intentionally visible:

- `provider-dimensions` means the core body and print dimensions came from the provider;
- `estimated` means at least one important dimension was inferred from an existing template/profile;
- `provisional` means the profile is useful for testing but still contains explicitly unverified body geometry;
- `generic` is the provider-neutral fallback.

A future Prodigi runtime provider ID such as `prodigi__H-MUG-W` resolves directly to the matching SKU calibration. An unknown Prodigi SKU does **not** borrow H-MUG-W geometry; it falls back to the generic profile until a specific calibration file exists.

### Adding another SKU

Add a JSON file to `src/mug_previewer/preview_v2/calibrations/`. Prefer physical inputs when known:

```json
{
  "id": "provider_product_sku_v2",
  "provider_name": "Provider",
  "product_name": "11oz Mug",
  "provider_profile_id": "provider__SKU",
  "sku": "SKU",
  "status": "provider-dimensions",
  "body_height_mm": 97.0,
  "body_diameter_mm": 82.0,
  "print_width_mm": 200.0,
  "print_height_mm": 90.0,
  "source_description": "Where these dimensions came from",
  "source_url": "https://provider.example/product",
  "verified_date": "YYYY-MM-DD"
}
```

When body and print dimensions are present, V2 derives body aspect ratio, printable-height fraction, and cylindrical print arc rather than duplicating hand-entered geometry values.

## Non-goals

V2 must never:

- modify authoritative prepared SVGs;
- compensate production artwork to make a mockup look centred;
- infer new bleed or safe areas from a mockup;
- replace provider-required output dimensions;
- become part of the export QA decision without an explicit calibrated rule.

The engineering view is there to expose geometry, not hide it.

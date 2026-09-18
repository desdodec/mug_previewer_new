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

V2 maps that flat strip onto a calibrated physical cylinder. The starting generic 11 oz calibration uses:

- a 300 degree printed arc;
- a 60 degree unprinted handle gap;
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
```

`models.py` owns calibrated mug geometry and camera/scene options.

`projection.py` expands the canonical wrap into a full 360-degree circumference strip with a transparent unprinted handle gap, then applies cylindrical projection.

`renderer.py` owns preview-only mug presentation and engineering guides.

## Calibration roadmap

The next useful step is empirical provider calibration rather than more arbitrary rendering polish.
For each provider/SKU we can store a preview calibration containing:

- body aspect ratio;
- printed arc / handle gap;
- printable height fraction;
- handle dimensions;
- default customer camera poses;
- optional photographic background/lighting preset.

Potential calibration targets:

- Inkthreadable 11 oz white mug;
- Printify fulfilment-provider-specific 11 oz mugs;
- Prodigi SKU-specific mugs.

Prodigi is especially suitable because its Product Details API can provide authoritative recommended print-area pixel dimensions while V2 separately owns physical/mockup calibration.

## Non-goals

V2 must never:

- modify authoritative prepared SVGs;
- compensate production artwork to make a mockup look centred;
- infer new bleed or safe areas from a mockup;
- replace provider-required output dimensions;
- become part of the export QA decision without an explicit calibrated rule.

The engineering view is there to expose geometry, not hide it.

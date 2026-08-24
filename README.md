# Mug Previewer

Mug Previewer consumes externally generated street datasets to produce customised mug artwork. The OpenStreetMap extraction workflow deliberately remains outside this repository.

## Architecture

`workflow_outputs_v6/<dataset>` is read by the workflow-v6 adapter and converted into typed `Dataset`, `StreetRecord`, capability, path, and statistics models. CLI inspection is available now; rendering, previews, exports, and GUI work are deferred to Task 02.

The adapter is the sole place that knows workflow-v6 filenames. It preserves persisted metric context metadata instead of retuning context-map behaviour.

## Requirements and setup

Python 3.11 or later is required.

```powershell
git clone https://github.com/desdodec/mug_previewer_new.git
cd mug_previewer_new
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Configure the external dataset root without committing it:

```powershell
$env:MUG_PREVIEWER_DATASET_ROOT="E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6"
python -m mug_previewer datasets list
```

Configuration precedence is explicit CLI `--dataset-root`, `MUG_PREVIEWER_DATASET_ROOT`, `local_config.toml` (or `MUG_PREVIEWER_LOCAL_CONFIG`), `.env`, then `config/default.toml`. Workflow outputs and generated artwork are ignored by Git.

## Commands

```powershell
python -m mug_previewer datasets list
python -m mug_previewer dataset inspect "E:\...\dataset"
python -m mug_previewer dataset validate "E:\...\dataset"
python -m mug_previewer dataset streets "E:\...\dataset" --search "Church" --limit 25

## Front-face rendering

The production renderer is `mug_previewer.rendering.face.render_face(street, options=None)`. It accepts a typed `StreetRecord`; workflow filenames, CSV files and glyph directories are resolved only by the dataset loader.

It returns an RGBA **495 × 462 px** PNG: the left/front half of V28's 990 × 462 fast-preview canvas. The image is a direct crop, retaining the original V28 glyph placement, face geometry, colour, line weights, typography and spacing. CairoSVG renders the SVG artwork and Pillow returns/saves the PNG.

The retained V28 text stack is `system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. On Windows this normally selects Segoe UI; it deliberately retains the original system-font fallback stack rather than bundling a system or commercial font.

```powershell
python -m mug_previewer render face `
  --dataset "E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6\20260824_101707_stoke_newington_streets_parks_water_boundary_clip" `
  --street-id 0246 `
  --output output\0246_face.png
```

Use `--area "…"` to override the display-area text; otherwise the dataset display name is used. `output/` is ignored by Git.

## Rear-context rendering

`mug_previewer.rendering.context_map.render_context_map(dataset, street, options=None)` returns the RGBA **495 × 462 px** rear half of V28's fast-preview canvas. It preserves the fixed 2:3 physical map artwork box and attribution layout, without composing a full wrap.

```powershell
python -m mug_previewer render context `
  --dataset "E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6\20260824_101707_stoke_newington_streets_parks_water_boundary_clip" `
  --street-id 0246 `
  --output output\0246_context.png
```

When the context SVG contains `rear-map-framing` metadata, the renderer uses the typed street metric bounds and dataset P90 span. The preserved policy is P90 × 1.75, clamped to 1400–2400 m, with 1.25× street padding and a maximum 1.35× expansion. SVGs without usable metric metadata remain supported through the legacy highlighted-street SVG-space crop. Full-wrap composition remains deferred.

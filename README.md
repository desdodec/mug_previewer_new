# Mug Previewer

Mug Previewer consumes externally generated street datasets to produce customised mug artwork. The OpenStreetMap extraction workflow deliberately remains outside this repository.

## Architecture

`workflow_outputs_v6/<dataset>` is read by the workflow-v6 adapter and converted into typed `Dataset`, `StreetRecord`, capability, path, and statistics models. The loader is the sole layer that knows workflow-v6 filenames and preserves persisted metric context metadata.

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
```

## Front-face rendering

`mug_previewer.rendering.face.render_face(street, options=None)` accepts a typed `StreetRecord` and returns an RGBA **495 × 462 px** front panel. It is a direct crop from V28's fast-preview canvas, retaining its glyph placement, face geometry, colour, line weights, typography, and spacing.

```powershell
python -m mug_previewer render face `
  --dataset "E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6\dataset" `
  --street-id 0246 `
  --output output\0246_face.png
```

Use `--area "…"` to override display-area text; otherwise the dataset display name is used.

The complete front composition (street name, locality, and face) is physically scaled to `1.18` and shifted down by `60 px`. Task 02N adds a shared `+4 px` locality-baseline adjustment to give the title/locality stack more breathing room; title styling, face placement, and all other internal relationships remain unchanged.

## Rear-context rendering

`mug_previewer.rendering.context_map.render_context_map(dataset, street, options=None)` returns an RGBA **495 × 462 px** rear panel. It contains the fixed 2:3 physical map artwork box and its OpenStreetMap attribution. Metric framing is applied inside this renderer before any wrap composition: P90 × 1.75, clamped to 1400–2400 m, with 1.25× street padding and up to 1.35× expansion. SVGs without usable metric metadata use the supported legacy SVG-space crop.

The final rear physical presentation scale is `1.20`, applied uniformly to the map-and-attribution group within the existing rear zone. This is separate from, and does not change, geographic framing.

The highlighted street retains its colour and geometry but uses an `0.85` visual stroke multiplier, so it remains immediately legible without overpowering map detail.

```powershell
python -m mug_previewer render context `
  --dataset "E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6\dataset" `
  --street-id 0246 `
  --output output\0246_context.png
```

## Full-wrap production rendering

`mug_previewer.rendering.artwork.render_wrap(dataset, street, options=None)` composes the completed front and rear panels into the template-v2 production master. It produces a transparent RGBA **2362 × 1063 px** PNG for the 20 × 9 cm, 300 ppi template. The two 945 px-wide handle-side print zones use uniform `contain` scaling; metric rear framing has already happened before composition.

The rear placement remains centred inside the fixed `(1417, 0, 945, 1063)` rear zone; the canonical canvas and seam exclusion remain unchanged.

```powershell
python -m mug_previewer render wrap `
  --dataset "E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6\dataset" `
  --street-id 0246 `
  --output output\0246_wrap.png
```

`output/` is ignored by Git. Provider-specific exports and web UI remain deferred.

## Desktop preview UI

The first desktop UI provides a local workflow for selecting a workflow-v6 dataset, filtering streets, and viewing front/rear production mug mockups. It uses standard-library Tkinter, so no new UI dependency or web server is needed.

```powershell
python -m mug_previewer.ui
# or
python -m mug_previewer --dataset-root "E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6" ui
```

The dataset root follows the existing precedence: the explicit `--dataset-root` argument, `MUG_PREVIEWER_DATASET_ROOT`, local configuration, then the current development workflow-v6 root. If the resolved root is absent or contains no usable datasets, the UI shows a concise error and remains open. Select a dataset, search/select a street, then choose **Render Preview**.

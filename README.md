# Mug Previewer

Mug Previewer consumes externally generated street datasets to produce customised mug artwork. The OpenStreetMap extraction workflow deliberately remains outside this repository.

## Release candidate 0.1.0rc2: install and use

Python 3.11 or later is required. Workflow-v6 datasets are external input and are never bundled with the application. Install a built release wheel into a clean environment, then configure its dataset root using a path appropriate to your machine:

    py -3.11 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install .\dist\mug_previewer-0.1.0rc2-py3-none-any.whl
    $env:MUG_PREVIEWER_DATASET_ROOT = "C:\path\to\workflow_outputs_v6"
    mug-previewer ui

The dataset root can also be supplied with the --dataset-root option, in local_config.toml (or the file named by MUG_PREVIEWER_LOCAL_CONFIG), or in .env. Precedence is explicit CLI option, environment variable, local config, .env, then config/default.toml.

In the desktop application, select a dataset and street, read its production status, preview a ready record, and choose the appropriate provider export. For Manual Review Required, use Review Selected Street, compare the standard and current edit, then approve a standard placement or the constrained edit. Preview changes are not saved until approval. Saved choices live in data/manual_overrides.json; clearing a decision returns that record to the pending queue.

Inkthreadable exports are 2362 x 1063 RGBA PNGs at 300 DPI. The bundled generic Printify 11oz profile exports 2475 x 1155 RGBA PNGs at 300 DPI.

Generated diagnostics/ evidence is local-only, excluded from package data, and normally ignored by Git. The packaged manual_review_scope.json is a version-controlled release manifest; refresh it only as a reviewed release change from validated production-triage results, never during application startup.

For instant review of prepared assets, point the UI at a preprocessing output. In this mode, street selection reads `preprocess_index.json` and its cached PNG directly; it does not recalculate production status or render a new mug preview.

```powershell
mug-previewer --dataset-root "E:\path\to\workflow_outputs_v6" --preprocessed "E:\path\to\preprocessed" ui
```

## Release limitations

- Manual edits are constrained to 0 or 180 degrees, discrete scale steps, and discrete vertical positions.
- Some valid streets require human review before export.
- Malformed face anatomy cannot be repaired by manual editing.
- Extreme unsupported title widths remain unrenderable.
- A compatible external workflow-v6 dataset is required for production work.

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

Task 02Q keeps that face calibration intact while anchoring the title/locality block 12 px upward in the pre-group front-panel coordinate system. Street titles use measured SVG fitting with only three sizes: 34.0 px, 30.0 px, and 26.0 px, selected against a shared 400 px safe width. The locality remains fixed at 18.0 px and the existing +4 px title/locality gap remains unchanged.

## Rear-context rendering

## Manual face review and durable overrides

Manual overrides live outside generated `diagnostics/` in the versioned JSON
store `data/manual_overrides.json`.  They are keyed by the stable workflow
dataset ID plus street ID; the street name is audit-only.  Saving is atomic and
one save deterministically replaces the previous decision for that key.

Automatic diagnostics, production triage, and human decisions remain separate.
Production first rejects malformed input, then applies an explicitly approved
manual decision, then falls back to automatic triage.  A pending review item
never enters final automatic wrap output.

The editor is a focused CLI workflow using the production face renderer.  It
allows only `0`/`180` degrees, scales `1.00, 0.95, 0.90, 0.85, 0.80`, and Y
offsets `-60` through `+60` in 20 px steps.  There is no free placement or
geometry editing.

```powershell
# Pending review items (use --include-resolved to audit prior decisions)
python -m mug_previewer manual-review list --dataset 'E:\...\dataset'

# Inspect STANDARD, diagnostic candidate, reason codes, and current state
python -m mug_previewer manual-review show --dataset 'E:\...\dataset' --street-id 0001

# Render an unsaved production-faithful edit, or use --load-diagnostic
python -m mug_previewer manual-review preview --dataset 'E:\...\dataset' --street-id 0001 `
  --orientation 180 --scale 0.95 --y-offset 20 --output output\0001_manual_preview.png

# Persist either canonical STANDARD or the reviewed transform
python -m mug_previewer manual-review approve-standard --dataset 'E:\...\dataset' --street-id 0001
python -m mug_previewer manual-review approve-transform --dataset 'E:\...\dataset' --street-id 0001 `
  --orientation 180 --scale 0.95 --y-offset 20 --note 'Raised mouth for nose clearance.'

# Return the street to pending review
python -m mug_previewer manual-review clear --dataset 'E:\...\dataset' --street-id 0001
```

`render face` and `render wrap` read this store by default (override it with
`--manual-overrides`).  The same approved front panel therefore flows unchanged
into canonical wrap rendering, mug previews, Inkthreadable, and Printify
exports.  The manual-review batch summary separately reports automatic
STANDARD/ADAPTED, manually approved STANDARD/OVERRIDE, pending review, and
unrenderable input.

## Production placement triage

`STANDARD`, `ADAPTED`, and `UNRESOLVED` remain diagnostic placement concepts.
Production use adds a conservative decision layer: `AUTO_APPROVED` (with
either `STANDARD` or `ADAPTED` placement), `MANUAL_REVIEW`, and
`UNRENDERABLE_INPUT`. A transformed street is only automatically approved
when canonical placement has a real defect and the bounded transform removes
it without weakening the facial relationship. Manual review is intentional:
the system does not attempt to force every street into automatic conversion.

Use the batch command to create a provider-neutral manifest before exporting:

```powershell
python -m mug_previewer diagnostics production-triage `
  --dataset 'E:\...\dataset' `
  --output-dir diagnostics\production_triage
```

The CSV records the diagnostic class, production status, selected transform,
and machine-readable reason codes. `MANUAL_REVIEW` and `UNRENDERABLE_INPUT`
records must be explicitly handled by a caller rather than treated as an
automatic production result.

## Manual-review handoff

`MANUAL_REVIEW` is an intentional production outcome, not a failure. Generate
the human queue directly from a dataset:

```powershell
python -m mug_previewer diagnostics manual-review `
  --dataset 'E:\...\dataset' `
  --output-dir diagnostics\task_03j_manual_review
```

This writes `manual_review_manifest.csv`, `summary.json`, an explicit
`unrenderable_input.csv`, and one deterministic comparison PNG per review
item in `comparisons\`. Each PNG shows canonical STANDARD and the best
diagnostic candidate, both labelled as not automatically approved. The manifest
retains the candidate's orientation, scale, and offsets alongside diagnostics
and reason codes, so a future human editor can choose a bounded transform and
re-render it deterministically.

The automatic production path contains only `AUTO_APPROVED` STANDARD or
ADAPTED decisions. Review records do not enter automatic final export; malformed
or unsupported input remains separate in `UNRENDERABLE_INPUT`.

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

`output/` is ignored by Git. Provider-specific exports are available through the desktop workflow; a web UI is not part of this release.

## Provider profiles

`mug_previewer.providers` contains data-driven provider/product delivery
specifications for future production export. A `ProviderProfile` describes a
provider canvas, DPI, colour metadata, accepted formats, and only those
printable or safe bounds the provider actually specifies. Built-in JSON files
live in `src/mug_previewer/providers/profiles/` and are loaded through package
resources:

```python
from mug_previewer.providers import get_provider_profile, list_provider_profiles

profile = get_provider_profile("inkthreadable_11oz_white")
profiles = list_provider_profiles()
```

```text
DesignOptions
      ↓
canonical 2362×1063 artwork
      ↓
ProviderProfile
      ↓
future production exporter
```

Profiles are downstream metadata only: they do not change canonical artwork or
mug-preview rendering. The Printify profile is explicitly generic because
dimensions and requirements can vary by fulfilment provider.

## Single-design production export

### Desktop Inkthreadable export

**Export Inkthreadable PNG** saves a fresh render of the selected street using
the current design controls and the production Inkthreadable profile. It writes
a `2362 x 1063` RGBA PNG at 300 DPI, leaving any already stale on-screen preview unchanged.

`mug_previewer.exporting.prepare_provider_image(wrap, profile)` turns one
completed canonical master into a provider delivery image. It is deliberately
downstream of rendering:

```text
render_wrap(...)
  canonical 2362x1063 RGBA
  ProviderProfile
  prepare_provider_image(...)
  save_provider_export(...)
```

The source must be the canonical 2362x1063 master. Provider dimensions never
feed back into the renderer. A matching target takes a copy-only fast path;
otherwise the exporter uses uniform contain scaling, integer-centred placement
(extra pixels fall right/bottom), and transparent padding for profiles whose
background policy supports it. The default format is each profile's preferred
format and saved files carry the profile DPI. PNG preserves alpha. Printify
JPEG requires an explicit `ExportOptions(jpeg_background=(r, g, b))` when the
prepared image contains transparency.

Inkthreadable exports remain 2362x1063 RGBA PNG with no resampling.
Export Printify PNG saves a fresh render through the generic
printify_generic_11oz_ceramic profile, producing a 2475x1155 transparent RGBA
PNG at 300 DPI with the full master and provider padding. This generic Printify
11oz ceramic profile is not universal: individual Printify fulfilment providers
may require different templates.

The older `export_wrap` / `save_provider_png` Gelato API remains an isolated
compatibility path for its legacy 2362x1134 Gelato specification. New callers
should use the ProviderProfile API above. A future Gelato ProviderProfile can
migrate that path without changing canonical artwork.

## Desktop preview UI

The first desktop UI provides a local workflow for selecting a workflow-v6 dataset, filtering streets, and viewing front/rear production mug mockups. It uses standard-library Tkinter, so no new UI dependency or web server is needed.

### Desktop Manual Review

Use **Manual Review** in the desktop app after its datasets have loaded. The default queue contains only pending MANUAL_REVIEW records, in deterministic dataset/ID order; the filter can switch to resolved records or all review records. It displays real production-face renders side-by-side for **STANDARD** and the current in-memory edit.
The accepted production review scope loads as lightweight metadata with a visible processed count, so the window opens immediately; production triage and both face previews are deferred until a reviewer opens a street. Switching filters reuses the session queue and does not rescan source streets.

Only approved controls are available: orientation 0 or 180 degrees, scales 1.00 through 0.80 in the supported steps, and vertical positions from -60 to +60 in 20 px steps. Unsaved adjustments belong only to the active street and are discarded when the reviewer navigates away; they are never written until an approval action. **Use Standard**, **Use Best Candidate**, and **Reset Edit** never save. Use **Approve Standard** or **Approve Current Edit** to atomically persist a decision; successful saves move to the next queue item. **Leave Pending** moves on without a write. Resolved records can be opened through the filter and **Clear Saved Decision** returns one to pending review after confirmation.


```powershell
python -m mug_previewer.ui
# or
python -m mug_previewer --dataset-root "E:\Python_Stuff\OS_Mail_Addresses\workflow_outputs_v6" ui
```

The dataset root follows the existing precedence: the explicit `--dataset-root` argument, `MUG_PREVIEWER_DATASET_ROOT`, local configuration, `.env`, then `config/default.toml`. The shipped default intentionally has no machine-specific dataset path. If the resolved root is absent or contains no usable datasets, the UI shows a concise error and remains open. Select a dataset, search/select a street, then choose **Render Preview**.

## Desktop production workflow

Prepared artwork opens in the Mug Workspace. Choose a dataset, search, and use
All, Production Ready, Manual Review, QA Attention, Do Not Use, or Unrenderable
to navigate. Counts use the existing batch classification and load in the
background. Filters never change batch scope: batch exports include the whole
selected prepared dataset.

The center tabs show Face, Mug Front, Mug Rear, and Full Wrap. Selecting a street
loads only its cached face image. **Preview Current SVG for QA** displays the exact
SVG for human review; **Preview Mug** explicitly composes the authoritative
prepared artwork without regenerating the face.

The right panel separates production state, QA state, and export eligibility.
Use the existing Inkscape edit, repair, and approval actions for manual artwork.
Approving changed artwork makes an old QA pass stale. **Refresh Artwork** reloads
external QA changes and workflow counts. Inkthreadable and Printify exports
require production approval, a valid authoritative SVG, and a current exact-SVG
QA pass. The Batch export tab retains background planning, progress, and cancellation.

`svg_review_results*.json` is authoritative production QA. `review_index.json`
is a compatibility snapshot; corruption in it alone does not block a valid batch.
Malformed or ambiguous authoritative review results still block production.

The reusable single-item APIs in `mug_previewer.preprocessed_export` are
`render_authoritative_face_panel`, `render_preprocessed_wrap`, and
`export_preprocessed_provider_png`. Preprocessing and export share
`rendering.svg_raster.rasterize_face_svg`. Catalogue UI metadata stays in its
existing location and the Workspace reuses these APIs.


## Manual SVG workspace (Task 04B)

In preprocessed mode, the Artwork and Review panel supports this workflow:

1. Select a `MANUAL_REVIEW` street; use **Manual Review only** to narrow the list.
2. Click **Edit in Inkscape**. The first click copies the generated SVG to a
   deterministic `*_edit.svg`; later clicks reopen the same working file.
3. Make changes and save the working file in Inkscape.
4. Return to Mug Previewer and click **Preview Edit**.
5. Click **Repair Edit** to restore the canonical page dimensions, viewBox and
   outer composition transform while preserving supported internal edits.
6. Inspect **Corrected SVG Preview** (also available through **Preview Corrected**).
7. Click **Approve Corrected**. Production changes to `MANUAL_APPROVED`, the cached
   preview refreshes immediately, and provider exports use the exact approved SVG.
8. Use **Next Manual Review** to advance in the current filtered order. It stops
   after the last later review item and reports that none remain; it does not wrap.

**The generated original SVG is never edited by the Workspace.** Working copies
are derived from the indexed generated filename, corrections live in its
`corrected/` subfolder, and approval uses the existing approval API. Repeated
repairs replace only the corrected derivative, atomically. Invalid or outdated
corrections cannot be approved. The command-line repair helper still refuses to
replace existing output unless its Python API is explicitly given `replace=True`.

For `MANUAL_APPROVED` artwork, **Edit Again in Inkscape** reopens the retained
working edit (or creates one from the generated original if it is missing).
Existing approved artwork stays authoritative until **Approve Corrected** succeeds
again. Failed approval preserves the previous production artwork and preview.
**Preview Current SVG for QA**, **Open Artwork Folder**, and **Refresh Artwork** are available
in the panel. Refresh or another street selection restores the normal generated
or approved cached preview. Street selection does not generate faces, score
candidates, or render rear artwork, wraps or mugs.

Inkscape is discovered using PATH, Windows App Paths registration (including
installations on other drives), then normal Windows install locations under
Program Files, Program Files (x86), and Local AppData. If it cannot be found, the
app opens a file picker. **Choose Inkscape executable** also allows an explicit
override, which is reused for this application session. The editor opens without
blocking the app. No machine-specific executable path is committed or persisted.

### Production and human QA are separate

**Production export requires an explicit current `pass` review for the exact authoritative SVG.**
Production approval and human QA are separate decisions. `preprocess_index.json`
holds production classification. Save browser review results alongside it as
`svg_review_results.json` or `svg_review_results_<label>.json`. The label is only
for organising files: identity comes from dataset/street IDs or unambiguous
indexed SVG paths, never from display-name spelling.

The backend normalizes those human sources on read and compares the reviewed
SVG SHA-256 with the authoritative SVG bytes. `review_index.json` is retained as
a compatibility snapshot for desktop review saves; it cannot grant production
permission without the human source. No separate import or ledger maintenance
is needed. See [Exact-artwork QA workflow](docs/qa_production_gate.md).

**Save Review** applies to the labelled artwork currently shown. Normal review
uses the approved SVG for `MANUAL_APPROVED`, or the indexed/generated SVG
otherwise. Explicit working/corrected previews change the review target only;
they do not save QA automatically or alter production authority. If an explicitly
reviewed working file differs from production artwork, its formal QA is stale.
If the file changes after preview, preview it again before saving a review.

Export rules for individual providers:

- No review blocks export, even for AUTO_APPROVED or MANUAL_APPROVED artwork.
- A current `pass` allows export when production is approved.
- Current problem flags (`overlap`, `duplicates`, `missing`, `other`, `Do Not Use`)
  block export. They do not delete artwork or change production classification.
- Any stale review, including an old pass, blocks export pending another review.
  The panel shows **(stale) - artwork changed; QA attention required**. An obsolete
  problem flag is therefore a request to review again, not a judgement of new art.
- `MANUAL_REVIEW` and `UNRENDERABLE_INPUT` remain unavailable for production export.

The exporter rechecks QA before using the existing Task 04A production path:
`resolve_authoritative_face_svg` -> approved SVG -> `render_authoritative_face_panel`
-> `render_preprocessed_wrap` -> provider export. No alternate renderer is used.

The standalone reviewer hashes the actual loaded SVG bytes. Embedded legacy SVG
snapshots can establish the same hash; filename-only reviews must be refreshed.
New files and changed artwork start unreviewed. Legacy live-render production
exports are blocked because they have no reviewed authoritative SVG. Use
preprocessed mode for production. Task 04D remains outside this change.

## Production-ready batch export

In preprocessed mode, select a dataset and open **Production batch...** to plan
and export its ready artwork through Inkthreadable or Printify. Existing files
are skipped by default. The batch window shows eligibility totals, progress,
cancellation, and an auditable report. Search and Manual Review-only filters do
not restrict batch scope. See [Batch export workflow](docs/batch_exports.md).

Desktop **Save Review** requires **Preview Current SVG for QA** (or an explicit
working/corrected preview) first. A cached PNG alone cannot establish which SVG
bytes were seen. Saving checks the hash of the bytes actually loaded for review;
if they changed, preview again. This adds no rendering to ordinary street browsing.

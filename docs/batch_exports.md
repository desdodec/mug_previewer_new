# Production-ready batch export

In preprocessed mode, select a dataset and open **Production batch...**. The
small batch window leaves the existing cached street browser in place.

1. Select **Inkthreadable** or **Printify**.
2. Choose an explicit destination folder. Outputs go in a provider-ID subfolder.
3. Leave **Skip existing** selected, or explicitly choose **Replace existing**.
4. Select **Refresh Batch Plan** to read the latest preprocessing index and QA
   ledger. The panel shows every eligibility count and existing-file count.
5. Select **Export N Production-Ready PNGs** to start the background worker.
6. Use **Open Export Folder** or **View Export Report** after the run.

Batch scope is every prepared record in the currently selected dataset. Search
and **Manual Review only** filters affect browsing, never batch scope. Nothing
exports automatically when opening the app, selecting a dataset, reviewing, or
approving artwork. Refresh the plan explicitly after review/approval changes.

## Eligibility

| Category | Rule |
| --- | --- |
| READY | AUTO_APPROVED or MANUAL_APPROVED, valid authoritative SVG, and either no QA record or a current pass. |
| MANUAL_REVIEW | Artwork requires manual approval; no export. |
| QA_BLOCKED | Approved artwork with overlap, duplicates, missing, other, or any stale review; the specific status is reported. |
| EXCLUDED | Do Not Use, including a stale Do Not Use review; intentional exclusion, never deleted. |
| UNRENDERABLE | UNRENDERABLE_INPUT; source/input unusable, no regeneration. |
| ASSET_ERROR | Missing/invalid authoritative artwork, invalid production state, failed preprocessing record, or prepared street missing from the source dataset. |

Do Not Use takes precedence over ordinary workflow categories. Invalid global
preprocessing structure, duplicate identities, filename collisions, or an invalid
QA ledger prevent planning. Asset errors are displayed prominently alongside
READY counts; unrelated ready items may still export, with every error retained
in the report. Production classification and human QA remain separate.

## Production authority

`execute_batch_export` calls `export_preprocessed_provider_png` exactly once for
each eligible attempted item. That exporter retains the Task 04A authoritative
SVG composition and Task 04B backend QA protection. There is no batch renderer,
preprocessing fallback, automatic repair, approval, or QA clearing.

Planning resolves artwork through `resolve_authoritative_face_svg` and compares
QA against that authoritative path through `current_review_state`. Manual
approval uses the indexed approved SVG; working edits and corrected drafts never
become production inputs automatically. Provider dimensions, DPI and colour mode
come from the existing provider profiles, which are unchanged.

## Safe destinations and filenames

The deterministic format is `{dataset_id}_{street_id}_{street-name}.png`.
Examples for a dataset ID of `hebden`:

- `hebden_0146_Mytholm_Close.png`
- `hebden_0001_Acre_Villas.png`
- `hebden_0002_Acres_Lane.png`
- `hebden_0004_Albert_Street.png`

Unicode is preserved. Windows-illegal characters, control characters, repeated
whitespace/separators, empty names, trailing periods/spaces and reserved device
names are handled centrally. Street IDs remain present. Case-insensitive
collisions are rejected before output starts, and overlong filename components
are reported as asset errors.

Skip existing is the default. Exporting occurs in a temporary directory beside
the destination, retaining the final filename for the single-item exporter.
Successful output is published atomically: a same-volume hard link in skip mode
prevents overwriting even if another process creates the final file during
rendering; explicit replace mode uses `os.replace`. An old PNG survives a failed
replacement. Filesystems that cannot publish with hard links fail safely and
report the error. Temporary output is cleaned after each attempted item.

## Changes during a batch

Immediately before exporting each READY item, the executor reloads both indexes,
re-resolves the authoritative SVG, and compares production metadata, authoritative
path and SHA-256, and the full QA record/stale flag against the plan. Changes
produce FAILED with a rebuild-required reason. These checks run again after the
single-item exporter returns, before publishing its temporary PNG. Changes to
artwork or QA during rendering therefore also prevent publication when detected.

The dataset/provider/destination controls are disabled during planning and
execution; duplicate launches are blocked. Review and edit actions remain
available and are guarded by the checks above. Normal street selection still
loads cached previews without production rendering.

## Cancellation and reports

**Cancel Batch** lets the current item finish and marks remaining ready items
CANCELLED. Finished PNGs remain intact, and a report is still written. Closing the
main app also requests cancellation; its non-daemon export worker finishes the
current item and writes the report before the process exits. Closing only the
batch window hides it, allowing a running batch to continue.

Each executed run atomically writes `batch_export_report.json` (schema version 1)
in its provider folder. This is the latest-run report and replaces an earlier
report in that folder. Keep separate destination folders when retaining reports
from multiple runs is important.

The report includes provider/dataset IDs, start/end timestamps, overwrite policy,
cancellation, planned and actual counts, and every planned item. Item fields
include street ID/name, production state, QA status/stale flag, eligibility,
authoritative SVG path/SHA-256, destination, result, and specific reason/error.
Exported hashes identify authoritative production artwork, not working drafts.
Eligibility skips, existing-file conflicts, runtime failures and cancellation
remain distinct. Progress-observer errors are recorded without abandoning the
batch. A report-write failure is surfaced to the UI; it never removes PNGs that
were already successfully exported.

Task 04D layout consolidation and browser-review JSON import are outside this
change.

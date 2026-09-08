# Included face batch export

The **Export PNG** tab plans every prepared record in the selected dataset,
regardless of search/grid filters. Counts show Included, Excluded, Unrenderable,
Asset errors and Existing outputs. Choose Inkthreadable or Printify and a destination,
then export the included PNGs. Existing outputs are skipped by default. The report
names every item and its export/skip/failure reason. Export rechecks artwork and
exclusions before publishing through the existing provider pipeline.


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
path and SHA-256, the full QA record/stale flag, reviewed SVG hash, and all review-source byte fingerprints against the plan. Changes
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

See [Face inclusion and legacy reviews](qa_production_gate.md) for migration and exclusion rules.

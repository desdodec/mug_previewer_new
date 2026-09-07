# Task 04C.1 validation

Production export requires an explicit current pass for the exact authoritative SVG.
Task 04D was not started. Provider dimensions, rendering geometry, context framing,
text layout, 8 px clearance and 20 px maximum automatic shift are unchanged.

## Git

- Branch: `codex/task-01-foundation` (no new branch).
- HEAD before: `541071d34bc3c1a4485d8f2aadc3685f2fa2226e`.
- Working tree before implementation: clean, verified.
- Final commit, push result and working-tree status: recorded in the delivery response.

## Schema and architecture

The actual `svg_review_results_hebden_bridge.json` contains an array of 224 records.
Every record has exactly `svg_name`, `svg_path`, `status`, and `preview_data_url`.
No record has stable IDs or reviewed_svg_sha256. Embedded snapshots contain
percent-encoded UTF-8 SVG, including CRLF line endings.

The central review-results layer discovers generic/per-dataset files in the
prepared root, resolves stable IDs first or unambiguous indexed paths, derives
snapshot hashes without text normalization, and produces normalized QA state.
Browser sources are production authority. The old review_index.json is retained
as a compatibility snapshot; it cannot restore a deleted browser pass. Desktop
Save Review updates the matching browser source and includes an exact SVG snapshot.
Parsing is cached by exact source and index bytes, never by a loose filename.

See [QA workflow](qa_production_gate.md) for formats, errors and user steps.

## Tests

- Focused QA, editing, exports and UI: **212 passed**, 1 existing warning, 46.21 s.
- Final full pytest: **398 passed**, 20,003 existing warnings, 489.88 s (8 min 9 s).
- JavaScript controller: actual loading, SHA, explicit status selection, stable
  identity recovery, JSON save/restore and stale-pass reset passed under Node.
- Browser/Python hashes agree for identical LF, CRLF, Unicode and BOM payloads.
- Patch whitespace check: passed.

The focused suite includes the automatic/manual pass-only matrix, every human
status, no review, stale hashes, malformed data, filename-only legacy decisions,
embedded SVG compatibility, ambiguous names, duplicate identities, conflicting
hash/snapshot evidence, and per-dataset discovery without display-name guessing.

Single exports are refused before rendering unless production and exact-SVG QA
both permit them. Staged PNGs are checked again before publication; changed review,
removed source and changed artwork leave existing destinations intact.

Batch tests retain existing-file handling, explicit replacement, atomic publication,
runtime failure continuation, filenames/collisions, profiles, reports, cancellation,
filter-independent scope and unfinished-edit protections. Explicit source-change
cases cover pass becoming overlap, review removal, source deletion, source-byte
changes and artwork changes, both after planning and during rendering.

The Edit Again regression exports approved A with pass A while edits are unfinished.
After Repair/Approve Corrected publishes B, single and batch exports block until
B receives its own pass. Both routes then succeed using the existing renderer.

The desktop cached-PNG regression prevents saving a pass for unseen SVG bytes:
Preview Current SVG for QA is required, and saving verifies the bytes actually read
against the hash captured when that SVG was previewed. Ordinary browsing stays cached.
Legacy live-render provider helpers and buttons cannot bypass the gate.

## Real Hebden Bridge audit

**224 human review records and 225 prepared street records processed: 449 record
checks in total. No audit records skipped. No production PNGs exported.**

| Human status | Records |
| --- | ---: |
| pass | 203 |
| overlap | 5 |
| duplicates | 0 |
| missing | 0 |
| other | 4 |
| Do Not Use | 12 |
| unresolved/ambiguous | 0 |

| Prepared-artwork outcome | Records |
| --- | ---: |
| Production-approved + current pass | 132 |
| Production-approved but not reviewed | 0 |
| Production-approved but non-pass | 12 |
| Stale/hash mismatch | 0 |
| Manual review | 80 |
| Unrenderable | 1 |
| Asset errors | 0 |
| Total prepared | 225 |

All 93 non-exportable streets are individually named with IDs and specific reasons
in [the per-street audit](qa_hebden_bridge_audit.json). The other 132 are eligible;
the audit itself does not publish PNGs. Manual-review streets remain blocked even
when their SVG has a human pass.

The human review file was not modified. Its SHA-256 before and after validation:
`cf706266a5acb4b5bae8f210b2cac71b5485bcfd3f4df23bfa9a307bb827ef55`.
The prepared index hash is recorded in the audit. No preprocessing or real manual
approvals were performed.

Metadata-only performance check: 100 prepared-street lookups averaged **6.38 ms**;
cold source normalization took **0.405 s**. No artwork rendering was invoked.

## Unavailable or omitted validation

- Live visual browser inspection: unavailable. The browser automation kernel exited
  twice before opening the reviewer, reporting `helper_unknown_error: setup refresh
  had errors`. Automated JavaScript controller and byte-hash checks passed.
- Optional real production sample export: omitted. Read-only real classification
  and synthetic provider PNG regressions establish the gate without publishing
  production assets. The requested large production export was not performed.

## Files changed

27 files changed:

- `README.md`
- `docs/batch_exports.md`
- `docs/qa_hebden_bridge_audit.json`
- `docs/qa_production_gate.md`
- `docs/task04c1_validation.md`
- `src/mug_previewer/batch_export.py`
- `src/mug_previewer/preprocessed_export.py`
- `src/mug_previewer/review_index.py`
- `src/mug_previewer/review_results.py`
- `src/mug_previewer/ui/app.py`
- `src/mug_previewer/ui/artwork_panel.py`
- `src/mug_previewer/ui/batch_export_panel.py`
- `src/mug_previewer/ui/state.py`
- `tests/reviewer_controller.cjs`
- `tests/test_artwork_panel.py`
- `tests/test_authoritative_svg_export.py`
- `tests/test_batch_export.py`
- `tests/test_batch_export_ui.py`
- `tests/test_manual_svg_workspace.py`
- `tests/test_pass_only_qa.py`
- `tests/test_preprocessed_ui.py`
- `tests/test_production_status_flow.py`
- `tests/test_review_index.py`
- `tests/test_ui_state.py`
- `tools/svg_reviewer/README.md`
- `tools/svg_reviewer/previewer.html`
- `tools/svg_reviewer/qa_hash.js`

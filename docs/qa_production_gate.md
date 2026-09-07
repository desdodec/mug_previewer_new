# Exact-artwork production QA

Production export requires an explicit current `pass` review for the exact authoritative SVG.
AUTO_APPROVED and MANUAL_APPROVED describe artwork approval, never human QA.

## Review and save

1. Open `tools/svg_reviewer/previewer.html` in Chrome or Edge.
2. Import a saved review array or the prepared `preprocess_index.json`. Select the
   matching SVG folder if local browser file restrictions prevent loading SVGs.
3. Review the SVG and choose pass, overlap, duplicates, missing, other, or Do Not
   Use. New or changed files start with a Not reviewed placeholder. A loaded old
   status is retained only when its exact snapshot/hash matches the loaded bytes.
4. Download JSON and replace the corresponding review file in the prepared root
   (normally `svg_previews`). Use `svg_review_results.json` or
   `svg_review_results_<label>.json`; for example `_hebden_bridge`,
   `_stoke_newington`, or `_glasgow`. Keep one review per dataset/street across
   these files. Move superseded copies out of the root; duplicate identities block.
5. Refresh Artwork or Refresh Batch Plan in the app. Only current passes on
   approved, valid authoritative SVGs enable the production buttons.

Exported arrays include SVG name/path, status, reviewed_svg_sha256, and a base64
SVG snapshot. Dataset/street IDs and street names are retained when supplied by
an index. IDs can also be recovered from the prepared convention
`faces/<dataset_id>/<street_id>_<name>.svg`. Selecting arbitrary loose files does
not invent IDs. Unreviewed/unavailable items are not exported as human decisions.
Filtering only changes visibility; it never restricts the saved reviewed set.

## One human source

`review_results.py` centrally discovers and validates human sources, resolves
identity, and normalizes exact-artwork QA in memory. It never infers a dataset ID
from the review filename. Stable IDs take precedence; legacy paths must match
indexed generated/approved SVG paths. A bare filename must match exactly one
identity across the prepared index. Duplicate or ambiguous identities fail closed.

`review_index.json` is retained for compatibility and desktop save snapshots.
Its old contents alone never grant production permission. Desktop Save Review
also updates the matching human browser source (or creates the generic source),
so there is no second production authority to maintain. Removing a browser
review cannot resurrect a pass from the old ledger. Production reads fresh source
bytes; parsing is cached only by those exact bytes and prepared index bytes.

## Legacy compatibility

The inspected Hebden Bridge file has a top-level array of 224 objects. Every
object has exactly `svg_name`, `svg_path`, `status`, and `preview_data_url`.
The snapshots use `data:image/svg+xml;charset=utf-8,` with percent-encoded SVG.
They contain neither stable IDs nor reviewed_svg_sha256. Paths resolve against
the prepared index; decoding snapshots preserves UTF-8 and CRLF bytes.

An SVG snapshot can derive a SHA-256. A PNG preview, filename, path, unsupported
status, malformed JSON, or missing exact hash cannot establish a production pass.
A supplied hash must agree with any embedded SVG snapshot. Hashes use raw bytes,
including BOMs and line endings: visually identical files are not interchangeable.
Browser SHA-256 and Python svg_sha256 are tested with identical Unicode, BOM,
CRLF and LF fixtures. Filename-only legacy decisions need a fresh human review;
loading a current file never silently transfers their old pass to new bytes.

## Safety and lifecycle

The backend reports PASS, NO_REVIEW, STALE_REVIEW, BLOCKED_STATUS,
AMBIGUOUS_REVIEW or INVALID_REVIEW. Only PASS permits export after production and
asset validation. Both provider buttons use this gate. Legacy live-render export
helpers fail with an instruction to use preprocessed artwork.

Single export stages its PNG and checks production state, authoritative path/hash,
QA status/hash and source fingerprints again before replacement. Batch planning
also binds the prepared record and checks immediately before rendering and before
publication. Any detected change requires refreshing/rebuilding the plan; old PNGs
survive failed replacements. Removing/changing review JSON is covered explicitly.

Edit Again keeps approved SVG A authoritative while a working edit is unfinished.
Its pass remains valid. Repair and Approve Corrected publish SVG B; A's pass then
becomes stale until B receives a new explicit pass. No rendering geometry,
provider dimensions, text layout or preprocessing rules change.

Desktop **Save Review** requires **Preview Current SVG for QA** (or an explicit
working/corrected preview) first. A cached PNG alone cannot establish which SVG
bytes were seen. Saving checks the hash of the bytes actually loaded for review;
if they changed, preview again. This adds no rendering to ordinary street browsing.

The reviewer regression tests require Node.js for the JavaScript controller and
Web Crypto cross-checks. Running the desktop app or standalone reviewer does not
require Node.js.

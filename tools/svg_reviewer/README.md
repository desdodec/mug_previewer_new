# SVG reviewer

Open `previewer.html` directly in Chrome or Edge. No server or build step is needed.
Use **Select SVG Files or Directory** to choose the SVG folder to review.
You can also import saved review JSON or `svg_previews/preprocess_index.json`.

Relative image paths in imported JSON resolve from the repository's
`svg_previews/` folder, preserving the original previewer location's behavior.
Saved reviews with embedded images can be opened without the original SVGs;
older reviews may require selecting the matching SVG folder.

The tool source belongs here. Generated SVGs, cached PNG previews, indexes,
and local review files belong in the ignored `svg_previews/` folder.

Production requires an explicit current pass for the exact SVG bytes. New or
changed artwork starts unreviewed. The updated reviewer exports byte-exact SHA-256
and base64 SVG snapshots, preserving stable identity where available. If local
file loading is blocked, use the SVG folder selector; PNG previews cannot be
reviewed as exact SVG evidence. Replace the corresponding review JSON in the
prepared root after downloading; do not keep duplicate decisions there. See
[Production QA workflow](../../docs/qa_production_gate.md).

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

# Front feature candidate diagnostics

This is experimental, developer-only analysis. It does not alter the production
face renderer, wrap rendering, UI previews, provider exports, or DesignOptions.

The scorer derives thresholded pixel masks from the current native face asset
and current typography placement. It tests only 0 and 180 degrees, scales of
1.00, 0.95, 0.90, 0.85, and 0.80, and vertical offsets from -60 to 60 in 20 px
steps. The default grid has 70 candidates. Horizontal offsets remain at zero
until evidence justifies widening the search.

Scores start at 100. Typography, clipping, eye, and mouth collision penalties
dominate; edge proximity, shrinkage, displacement, and 180-degree rotation
receive smaller explicit penalties. Raw overlap pixels and overlap/street-pixel
ratios are both retained. A 180-degree result must exceed the best 0-degree
candidate by five points before it is selected, preserving the original
orientation when differences are marginal.

Run this against an external workflow-v6 dataset:

    python -m mug_previewer diagnostics front-candidates --dataset "E:\...\dataset" --street-id 0246 --output-dir "$env:TEMP\mug_previewer_task_03e_candidates"

Repeat --street-id to generate one folder per street. Each folder contains
candidates.csv, current.png, best.png, and top_01.png through top_03.png.
PNGs are debug overlays: blue marks eyes, red marks mouth, yellow marks
typography, and green marks the transformed street mask.

The classification is diagnostic only: STANDARD accepts the current
composition, ADAPTED finds an acceptable modest adjustment, EXTREME requires a
larger bounded adjustment, and UNSUITABLE finds no acceptable candidate in this
grid.

# Front feature candidate diagnostics

This is experimental, developer-only analysis. It does not alter the production face renderer, wrap rendering, UI previews, provider exports, or `DesignOptions`.

The scorer derives thresholded pixel masks from the native face asset and current typography placement. It tests only 0 and 180 degrees, scales of 1.00, 0.95, 0.90, 0.85, and 0.80, and vertical offsets from -60 to 60 in 20 px steps. The default grid has 70 candidates. Horizontal offsets remain zero.

Collision remains decisive. In addition, the diagnostic uses an exact Euclidean distance transform of each thresholded eye and mouth mask. It samples the fifth-percentile distance beneath street pixels, rather than a bounding-box distance or a single antialiased fringe pixel. Mouth spacing has 12 px hard / 32 px comfortable thresholds; eye spacing has 9 px hard / 24 px comfortable thresholds. Penalties rise continuously inside the comfortable zone and become stronger inside the hard zone. Typography still uses literal overlap only.

The CSV contains raw collisions, the robust mouth/eye distances and panel-height ratios, each edge margin, and score decomposition: `collision_penalty`, `proximity_penalty`, `edge_penalty`, `scale_penalty`, `offset_penalty`, `rotation_penalty`, and `orientation_penalty_or_bonus`. Edge penalty is now graded only inside a 4 px margin (maximum 2 points); clipping remains a severe collision penalty.

Orientation diagnostics record vertical centroid, upper/lower-half mass ratios, and top/bottom-band widths. A weak interpretable bonus favours a broader upper/base band and upper mass over a narrower lower/tip band. This is intentionally bounded (7 points) and the existing 3 point rotation penalty plus 5 point conservative orientation gate remain. Thus symmetric or ambiguous features retain 0 degrees.

Run this against an external workflow-v6 dataset:

    python -m mug_previewer diagnostics front-candidates --dataset "E:\\...\\dataset" --street-id 0246 --output-dir "$env:TEMP\mug_previewer_task_03e2_outliers"

Repeat `--street-id` to create one folder per street. Each folder contains `candidates.csv`, `current.png`, `best.png`, and `top_01.png` through `top_03.png`. PNGs are debug-only overlays: blue marks eyes, red mouth, yellow typography, and green transformed street geometry.

The classification is diagnostic only: STANDARD accepts the current composition, ADAPTED finds an acceptable modest adjustment, EXTREME requires a larger bounded adjustment, and UNSUITABLE finds no acceptable candidate in this grid.
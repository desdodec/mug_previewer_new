# Front feature candidate diagnostics

This is experimental, developer-only analysis. It does not alter the production face renderer, wrap rendering, UI previews, provider exports, or `DesignOptions`.

The scorer derives thresholded pixel masks from the native face asset and current typography placement. All masks use alpha >= 16 in the final `(0, 0)`–`495 x 462` production-panel coordinate space: asset masks retain the exact native `<image>` placement and then use the frozen front-group transform; typography uses the production font, baselines, and that same group transform. It tests only 0 and 180 degrees, scales of 1.00, 0.95, 0.90, 0.85, and 0.80, and vertical offsets from -60 to 60 in 20 px steps. The default grid has 70 candidates. Horizontal offsets remain zero.

Collision remains decisive. In addition, the diagnostic uses an exact Euclidean distance transform of each thresholded eye and mouth mask and reports the minimum foreground-to-foreground distance. Overlap is `0 px`; ties select row-major street then protected pixels. Valid distances are bounded by `hypot(width - 1, height - 1)`. Empty or mismatched masks raise a diagnostic error; they are never converted into a plausible large distance. Mouth spacing has 12 px hard / 32 px comfortable thresholds; eye spacing has 9 px hard / 24 px comfortable thresholds. Penalties rise continuously inside the comfortable zone and become stronger inside the hard zone. Typography still uses literal overlap only.

The CSV contains raw collisions, mouth/eye distances, their nearest foreground pixel coordinates, panel-height ratios, each edge margin, and score decomposition: `collision_penalty`, `proximity_penalty`, `edge_penalty`, `scale_penalty`, `offset_penalty`, `rotation_penalty`, and `orientation_penalty_or_bonus`. `mask_integrity.csv` records final mask size, foreground count, bounds, and threshold. Edge penalty is now graded only inside a 4 px margin (maximum 2 points); clipping remains a severe collision penalty.

Orientation diagnostics record vertical centroid, upper/lower-half mass ratios, and top/bottom-band widths. A weak interpretable bonus favours a broader upper/base band and upper mass over a narrower lower/tip band. This is intentionally bounded (7 points) and the existing 3 point rotation penalty plus 5 point conservative orientation gate remain. Thus symmetric or ambiguous features retain 0 degrees.

Run this against an external workflow-v6 dataset:

    python -m mug_previewer diagnostics front-candidates --dataset "E:\\...\\dataset" --street-id 0246 --output-dir "$env:TEMP\mug_previewer_task_03e2_outliers"

Repeat `--street-id` to create one folder per street. Each folder contains `candidates.csv`, `mask_integrity.csv`, `current.png`, `best.png`, `alignment.png`, and `top_01.png` through `top_03.png`. PNGs are debug-only overlays: blue marks eyes, red mouth, yellow typography, and green transformed street geometry. `alignment.png` also draws street/mouth bounds and the reported nearest pair over the actual production face.

The classification is diagnostic only: STANDARD accepts the current composition, ADAPTED finds an acceptable modest adjustment, EXTREME requires a larger bounded adjustment, and UNSUITABLE finds no acceptable candidate in this grid.

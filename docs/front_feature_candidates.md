# Front feature candidate diagnostics

This is experimental, developer-only analysis. It does not alter the production face renderer, wrap rendering, UI previews, provider exports, or `DesignOptions`.

The scorer derives thresholded pixel masks from the native face asset and current typography placement. All masks use alpha >= 16 in the final `(0, 0)`–`495 x 462` production-panel coordinate space: asset masks retain the exact native `<image>` placement and then use the frozen front-group transform; typography uses the production font, baselines, and that same group transform. It tests only 0 and 180 degrees, scales of 1.00, 0.95, 0.90, 0.85, and 0.80, and vertical offsets from -60 to 60 in 20 px steps. The default grid has 70 candidates. Horizontal offsets remain zero.

Collision remains decisive. The diagnostic uses exact Euclidean foreground distance and reports `0 px` for overlap; ties select row-major street then protected pixels. Empty or mismatched masks raise a diagnostic error. Nose spacing is a saturating safety constraint: gaps below 4 px receive a strong penalty, gaps from 4 to 8 px receive a diminishing penalty, and 8 px or more receives no clearance reward or penalty. Typography follows the same principle with 6 px hard / 16 px healthy thresholds. Eye spacing remains 9 px hard / 24 px comfortable.

The CSV contains raw collisions, distances, their nearest foreground pixel coordinates, panel-height ratios, each edge margin, and score decomposition including `nose_clearance_penalty`, `typography_clearance_penalty`, and `mouth_role_penalty`. Mouth role uses the transformed street pixel-mass centroid against a lower-face target at 67% of the static-nose bounds, with a 70 px soft band. It is deliberately separate from the street's internal orientation descriptors. A one-point effective-tie band selects the least-transformed candidate among numerically equivalent results.

Orientation diagnostics record vertical centroid, upper/lower-half mass ratios, and top/bottom-band widths. A weak interpretable bonus favours a broader upper/base band and upper mass over a narrower lower/tip band. This is intentionally bounded (7 points) and the existing 3 point rotation penalty plus 5 point conservative orientation gate remain. Thus symmetric or ambiguous features retain 0 degrees.

Run this against an external workflow-v6 dataset:

    python -m mug_previewer diagnostics front-candidates --dataset "E:\\...\\dataset" --street-id 0246 --output-dir "$env:TEMP\mug_previewer_task_03e2_outliers"

Repeat `--street-id` to create one folder per street. Each folder contains `candidates.csv`, `mask_integrity.csv`, `current.png`, `best.png`, `alignment.png`, and `top_01.png` through `top_03.png`. PNGs are debug-only overlays: blue marks eyes, red mouth, yellow typography, and green transformed street geometry. `alignment.png` also draws street/mouth bounds and the reported nearest pair over the actual production face.

The classification is diagnostic only: STANDARD accepts the current composition, ADAPTED finds an acceptable modest adjustment, EXTREME requires a larger bounded adjustment, and UNSUITABLE finds no acceptable candidate in this grid.

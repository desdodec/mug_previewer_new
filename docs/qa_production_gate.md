# Face inclusion and legacy reviews

Create Faces ? Review / Edit Faces ? Export PNG.

Generate prepared faces for the selected dataset, then use the scrollable face
grid. It defaults to two columns, with column and card-size controls. Cards load
cached PNGs only; scrolling never generates or renders SVG artwork. Search and
All / Included / Excluded / Edited / Needs Attention filters affect browsing only.

Every valid prepared SVG is included by default, including MANUAL_REVIEW records.
Use **Exclude** to opt a street out. Choices persist in `face_exclusions.json` by
dataset/street identity, independently of SVG hashes. Legacy browser reviews and
`review_index.json` remain readable: pass means included; Do Not Use, overlap,
duplicates, missing and other mean excluded. An explicit checkbox choice overrides
legacy status. Invalid or ambiguous review identities remain errors.

Click **Edit in Inkscape** and save there. The app retains the generated original,
reuses a working SVG, detects saved changes, and runs the existing repair and
validation backend in the background. Valid corrected artwork automatically
becomes authoritative and receives an updated cached preview. Invalid edits
leave current artwork and its preview intact. Right-click an edited card to
**Revert to generated original**. Inkscape is discovered automatically; if absent,
the app asks for its executable. Keep the app open while editing for save detection.

The **Export PNG** tab plans every prepared record in the selected dataset,
regardless of search/grid filters. Counts show Included, Excluded, Unrenderable,
Asset errors and Existing outputs. Choose Inkthreadable or Printify and a destination,
then export the included PNGs. Existing outputs are skipped by default. The report
names every item and its export/skip/failure reason. Export rechecks artwork and
exclusions before publishing through the existing provider pipeline.

UNRENDERABLE_INPUT and invalid/missing SVGs remain blocked. Production states are
advisory; an exact-SVG PASS is no longer required. Face geometry, 8 px clearance,
20 px maximum automatic shift, rear map, mug composition and provider sizes are unchanged.

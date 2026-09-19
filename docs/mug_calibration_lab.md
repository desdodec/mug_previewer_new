# Mug Calibration Lab

Mug Calibration Lab is a separate desktop application for measuring how print-on-demand providers map flat mug artwork onto their generated mockups.

It does not render street artwork, alter prepared SVGs, or publish production exports.

## Launch

From an editable checkout:

```powershell
python -m mug_previewer.calibration
```

After reinstalling the package / editable environment, the console entry point is also:

```powershell
mug-calibrator
```

## Workflow

### 1. Choose the provider / SKU profile

Select the closest existing V2 calibration profile.

The app starts with Inkthreadable 11oz White Mug because the canonical Mug Previewer master originated from that 200 x 90 mm template.

### 2. Save the calibration target

Choose **Save Target PNG**.

The generated 2362 x 1063 PNG contains:

- angular longitude lines;
- horizontal percentage bands;
- exact FRONT and REAR mathematical centre bullseyes;
- asymmetric corner fiducials;
- the canonical 472 px handle/seam exclusion zone;
- explicit flat-canvas F0 / SEAM / R0 references.

Upload this exact image to the provider without cropping, scaling or editing it.

### 3. Obtain provider mockups

Generate/download both a front and rear provider mockup of the calibration target.

Use the provider's normal mockup generator. Do not manually rotate or crop the images before loading them into the lab.

### 4. Load front and rear mockups

Use:

- **Load Front Mockup**
- **Load Rear Mockup**

For each image, drag a rectangle tightly around the cylindrical mug body.

Do **not** include the handle.

The rectangle is the screen-space mug surface onto which the expected target projection is fitted.

### 5. Fit camera yaw first

Adjust:

- **Front camera yaw**
- **Rear camera yaw**

Ignore the shared artwork-registration control initially.

Fit the central bullseye and neighbouring longitude lines. Camera yaw describes how the provider's virtual/photographic camera is rotated relative to the intended face centre.

### 6. Fit visible cylinder arc

Adjust **Visible cylinder arc** until the spacing/compression of longitude lines matches the provider mockup.

A smaller/larger value changes how aggressively the artwork compresses toward the visible mug edges.

### 7. Fit print canvas arc only when evidence supports it

**Print canvas arc** is physical/calibration geometry, not merely a camera setting.

Change it only when the provider target gives consistent evidence that the current body circumference / print-width relationship is wrong.

### 8. Fit vertical projection

Adjust per view:

- vertical scale;
- vertical offset.

These account for the provider's mockup crop/perspective and should not be confused with production artwork placement.

### 9. Use shared artwork registration last

Use **Shared artwork registration** only if both front and rear views still require the same angular shift after their independent camera yaws are fitted.

That is the important distinction:

- front/rear yaw differences are camera/mockup pose;
- a common residual angular shift is evidence of artwork registration bias.

### 10. Save the session

**Save Session** stores all fitted parameters and mug-body bounds.

Provider mockup images are deliberately not embedded in the session file. Reload the images when reopening a session.

### 11. Export candidate calibration

**Export Candidate JSON** writes a provisional calibration candidate.

It does not modify:

```text
src/mug_previewer/preview_v2/calibrations/
```

The candidate retains:

- fitted front/rear camera yaw;
- shared artwork registration offset;
- fitted visible cylinder arc;
- fitted print canvas arc;
- mug bounds;
- per-view vertical scale/offset.

Review the candidate before promoting any values into a built-in provider profile.

## Interpreting results

A useful calibration normally has:

1. front/rear bullseyes aligned;
2. longitude-line compression matching on both views;
3. horizontal bands aligned vertically;
4. minimal need for shared artwork offset.

If the provider mockup cannot be fitted well with a cylindrical projection, that is meaningful evidence that the provider may be using a non-physical warp/displacement map. In that case the calibration should be described as an **effective provider mockup projection**, not a literal camera model.

## Safety boundary

Calibration Lab never:

- edits production SVGs;
- changes provider export dimensions;
- changes QA decisions;
- writes built-in calibration profiles automatically;
- treats a fitted candidate as verified.

Promotion of a candidate into the built-in profile directory is a separate reviewed code/data change.

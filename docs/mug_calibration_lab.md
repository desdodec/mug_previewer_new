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

A native supplier calibration PNG can also be generated without opening the GUI:

```powershell
mug-calibrator target --profile prodigi_h_mug_w --output prodigi_h_mug_w_calibration.png
```

The target dimensions, DPI and placement anchors come directly from the selected
production `ProviderProfile`.

## Workflow

### 1. Keep print calibration and preview calibration separate

The lab now exposes two related but different profile choices:

- **Provider / SKU calibration** selects the V2 mockup-projection model used for
  camera/yaw fitting.
- **Native supplier print target** selects the production `ProviderProfile`
  used to generate an upload file at the supplier's exact pixel dimensions and
  DPI.

When matching IDs exist, changing the V2 profile automatically selects the
corresponding production profile.

### 2. Generate the right target for the question

For supplier upload/fitting tests, use **Save Native Supplier Target**. This is
the preferred calibration artifact.

The native target is rendered directly at the supplier canvas, with no
canonical-master resize in between. For Prodigi H-MUG-W this is **2705 x 1122
px at 300 DPI**. It contains:

- numbered longitude fiducials every 2.5% of wrap width;
- horizontal metrology bands every 5% of height;
- exact provider-profile FRONT and REAR centre bullseyes;
- an independent 50% midpoint / opposite-handle bullseye;
- top/bottom 1% rulers;
- asymmetric orientation marks;
- colour patches and text baselines;
- visible supplier edge/seam zones;
- the provider ID, exact pixel dimensions and DPI printed into the image.

Upload that PNG unchanged. If the provider uploader still reports that it must
fit, crop or resize the image, that behaviour is itself calibration evidence.

Use **Save V2 Overlay Target** only when fitting the V2 virtual-camera model.
That legacy 2362 x 1063 canonical target is intentionally retained because the
overlay maths is expressed in the canonical preview coordinate system. It
should not be used to infer native supplier print-file dimensions.

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

### 9. Treat shared artwork registration as an advanced hypothesis

Use **Shared artwork registration** only when you have independent evidence constraining camera yaw, for example from handle geometry or a known provider camera pose.

A printed target alone cannot uniquely separate camera yaw from artwork registration: both move the projected target horizontally. The lab therefore exposes both parameters for controlled experiments but does not claim that a shared residual shift proves registration bias.

The directly measurable quantity is the provider's **effective projection**. Separating that effective shift into camera rotation and print registration requires additional evidence.

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
- treats a fitted candidate as verified;
- converts an angled mockup into a production offset automatically.

The native target generator reads the production profile; it does not modify it.
A supplier-specific production offset should only be promoted after repeatable
evidence, ideally including a physical printed sample.

Promotion of a candidate into the built-in profile directory is a separate reviewed code/data change.

# Mug Previewer 0.1.0rc4

- Fast desktop previews now reuse the validated auto-approved production placement instead of rerunning the 70-candidate scorer.
- Screen preview mockups render at 512x768, while production wraps and provider exports remain unchanged.
- Shutdown now invalidates active workers and guards Tk poll and resize callbacks against updates after the application closes.

## Previous release candidate: 0.1.0rc3
# Mug Previewer 0.1.0rc3

Fixes the preview-render Tk thread handoff: completed worker results are now queued and applied by the Tk main thread, preventing the desktop preview UI from remaining in `Rendering...`.

## Previous release candidate: 0.1.0rc2

# Mug Previewer 0.1.0rc2

Fixes a desktop UI issue where production status checking could remain indefinitely in a loading state after selecting a street, leaving preview and export actions disabled.

No rendering, provider geometry, or production-triage policy changes.

## Previous release candidate: 0.1.0rc1

This release candidate packages the validated desktop workflow for external
workflow-v6 street datasets.

## Included

- Front-face rendering and rear context mapping.
- Provider-aware wrap exports for Inkthreadable 11oz White and generic Printify
  11oz Ceramic products.
- Conservative automatic triage, a desktop manual-review editor, and durable
  approved placement overrides.
- Dataset selection, production status, preview, review, and export in the
  desktop application.

## Intentional limitations

- Manual placement uses only 0 or 180 degrees, discrete scale values, and
  discrete vertical positions.
- Streets requiring a judgement call remain in Manual Review.
- Missing input anatomy cannot be repaired in the editor.
- Street titles beyond the supported width are unrenderable.
- Users must provide an external workflow-v6 dataset; no source dataset is
  bundled in the package.

## Packaging policy

Runtime resources, including provider profiles, the manual-review scope, and
the owned mug-preview images, are packaged with the release. Generated
diagnostics, build products, and external datasets are excluded.

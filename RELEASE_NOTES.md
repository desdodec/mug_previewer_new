# Mug Previewer 0.1.0rc1

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

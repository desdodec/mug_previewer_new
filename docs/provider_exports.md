# Provider exports

## Canonical master versus provider file

`render_wrap(dataset, street)` remains the provider-independent artwork API.
It produces the transparent RGBA `2362 x 1063 px` template-v2 canonical master
at 300 ppi. Provider profiles are downstream transformations of that completed
image. They must not redefine face rendering, context-map framing, wrap
composition, panel placement, or canonical geometry.

## Supported profile

The initial and only profile is **Gelato — White 11oz Ceramic Mug**.

| Requirement | Profile value |
| --- | --- |
| Official printable area | 200 x 96 mm |
| Pixel canvas at 300 DPI | 2362 x 1134 px (`round(mm * 300 / 25.4)`) |
| Format / mode | PNG / RGBA |
| DPI metadata | 300 ppi, explicitly written on save |
| Transparency | Preserved; transparent padding is retained |
| Scaling | Uniform `contain`; no distortion |
| Canonical transform | Scale 1.0, 35 px top and 36 px bottom transparent padding |

Official specifications: [Gelato printable areas for mugs](https://support.gelato.com/en/articles/8996273-what-is-the-printable-area-for-mugs),
[Gelato mug resolution guidance](https://support.gelato.com/en/articles/10244779-optimizing-print-results-for-mugs-and-bottles), and
[Gelato upload formats](https://support.gelato.com/en/articles/8996346-in-what-format-can-i-upload-my-files).

## API

```python
from pathlib import Path

from mug_previewer.exporting import (
    GELATO_WHITE_11OZ_CERAMIC_MUG,
    export_wrap,
    provider_export_filename,
    save_provider_png,
)

provider_image = export_wrap(master, GELATO_WHITE_11OZ_CERAMIC_MUG)
path = Path("output") / provider_export_filename(
    street.id, street.display_name, GELATO_WHITE_11OZ_CERAMIC_MUG,
)
result = save_provider_png(master, path, GELATO_WHITE_11OZ_CERAMIC_MUG)
```

`save_provider_png()` sets PNG DPI metadata explicitly. Pixel dimensions and
DPI metadata are separate properties and are verified independently in tests.

# docs/figs — image assets

All images used by **README.md** / **README.zh-CN.md** / **docs/** live here. Do not delete without checking references.

## Brand / identity

| File | Format | Size | Purpose | Status |
| --- | --- | --- | --- | --- |
| `voxel_icon.png` | square 512×512 | ~36 KB PNG | **Current README hero logo**. Dark-badge identity (`iteration-3`): hex prism focusing a yield curve, amber peak marks the inspected claim. Deep-teal `#17211d` field (intentional solid background to define inner blocks). Note: the filename `voxel_*` is preserved only to keep README `src=` references stable; the mark itself is no longer voxel/pixel-art. | ✅ Active (hero) |
| `voxel_social.png` | wide 1280×640 | ~33 KB PNG | **GitHub social preview card**. The badge centered on the same deep-teal field. Used as the GitHub social preview image. | ✅ Active (social) |
| `logo.svg` | square 512×512 viewBox | ~1 KB SVG | **Source of truth** for the dark-badge identity. Editable vector. | ✅ Active (source) |
| `logo.png` | square 512×512 | ~36 KB PNG | Transparent-badge raster, general purpose (favicons etc. live in `logos/export/`). | ✅ Active |
| `logo_white_background.png` | square 512×512 | ~31 KB PNG | Same badge flattened onto the project warm-white `#fffdf8` field — for light-background contexts (legacy alt name kept for stable README `src=`). | ✅ Active (light variant) |

## Diagrams

| File | Format | Purpose | Status |
| --- | --- | --- | --- |
| `architecture.svg` / `architecture.png` | ~92% width | **Architecture diagram** (Question → Resolver → Planner → Tools → Evidence → Guardrail → Trust). Embedded in README Architecture section and in docs pages. | ✅ Active. Note: pre-redesign visual style; not yet restyled to the dark-badge identity. Clarity prioritized over brand. |

## Notes

- **Identity (2026-08 redesign)**: the mark is a dark-badge hex prism + yield curve with an amber peak. Source design artifacts (concepts, iterations, export sizes, preview page) live in `logos/` at repo root. The badge SVG baked into `logo.svg` here; PNG baked from that via headless Chromium + flood-fill transparency (script in `logos/export/`).
- **Why `voxel_*` filenames remain**: kept so README `src=` attributes don't churn. The visual is no longer voxel/pixel-art — the names are pure compatibility identifiers.
- **Legacy backups**: `docs/figs/_legacy_2026/` (gitignored) holds the pre-redesign wordmark PNG/SVG set, kept locally for one-step revert only. Not in the repo.
- The deep-teal background of `voxel_icon.png` is **intentional**: the badge is designed to sit on a solid field to define its inner blocks. It is not transparent by design.
- `architecture.*` predates the redesign. If visual consistency matters, it could be restyled to the dark-badge palette — but architecture diagrams prioritize clarity over brand, so restyling is optional.

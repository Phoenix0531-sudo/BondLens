# docs/figs — image assets

All images used by **README.md** / **README.zh-CN.md** / **docs/** live here. Do not delete without checking references.

## Brand / identity

| File | Format | Size | Purpose | Status |
| --- | --- | --- | --- | --- |
| `voxel_icon.svg` / `voxel_icon.png` | square 512×512 | 4 KB SVG / 2.7 KB PNG | **Current README hero logo**. Voxel / pixel-art style: lens over yield bars + bond strip on deep-teal field. | ✅ Active (hero) |
| `voxel_social.svg` / `voxel_social.png` | wide 1280×640 | 6.3 KB SVG / 203 KB PNG | **GitHub social preview card**. Embeds the voxel mark + "BondLens" wordmark + pipeline chips. Also deployed as `docs/screenshots/social_preview.png`. | ✅ Active (social) |
| `logo.svg` / `logo.png` | square | 1 KB SVG / 20 KB PNG | **Legacy wordmark** (pre-voxel). Kept as fallback / classic wordmark link in README hero ("classic wordmark"). | 🟡 Referenced as legacy fallback |
| `logo_white_background.png` | square | 20 KB PNG | **Byte-identical copy** of `logo.png` (same MD5: `244a1c46…`). Historical alt name for light-background contexts. | 🟡 Duplicate of `logo.png`; safe to dedupe if references are updated. |

## Diagrams

| File | Format | Purpose | Status |
| --- | --- | --- | --- |
| `architecture.svg` / `architecture.png` | ~92% width | **Architecture diagram** (Question → Resolver → Planner → Tools → Evidence → Guardrail → Trust). Embedded in README Architecture section and in docs pages. | ✅ Active. Note: pre-voxel visual style; not yet restyled to match voxel identity |

## Notes

- `voxel_*` assets are the current brand set (added 2026-08). Source is hand-authored SVG; PNG baked via headless Chromium (`voxel_icon.svg` used `shape-rendering="crispEdges"` for crisp pixel-art).
- `logo*` files are the legacy wordmark set. The README still links `logo_white_background.png` as the "classic wordmark" fallback; it and `logo.png` are identical, so the link could be repointed to `logo.png` to remove the duplicate (low priority, cosmetic).
- `architecture.*` predates the voxel redesign. If visual consistency matters, it could be restyled to the voxel/teal palette — but architecture diagrams prioritize clarity over brand, so restyling is optional.
- The deep-teal background of `voxel_icon.png` is **intentional**: the voxel / pixel-art style depends on a solid field to define the inner "blocks". It is NOT transparent; this is by design, not a bug.

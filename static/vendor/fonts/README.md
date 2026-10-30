# Self-hosted typography

BondLens bundles **Noto Sans SC Variable** (weights 100–900) for Simplified
Chinese interface text. Its upstream is Google Fonts / Noto CJK:

- Source: https://github.com/google/fonts/tree/main/ofl/notosanssc
- Original binary: `NotoSansSC[wght].ttf` from the official repository.
- Local browser asset: `NotoSansSC-VF.woff2`, converted from the official TTF
  using fontTools WOFF2 compression without altering the glyph outlines.
- WOFF2 SHA-256: `aef8c34277afad81ecd0227138a830263c0caea65b7aea66d1195395f097b55a`.
- License: SIL Open Font License 1.1, reproduced in `OFL.txt`.

The WOFF2 file is served by BondLens itself, so no external font service or
network connection is required at runtime.

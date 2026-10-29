# Vendored interface components

These files are pinned local copies so the BondLens demo works without a public
CDN connection. They are not authored by the BondLens project.

| Package | Version | Files | License | Upstream |
| --- | --- | --- | --- | --- |
| Tabler Core | 1.7.1 | `tabler/tabler.min.css` | MIT | https://github.com/tabler/tabler |
| Tabulator | 6.7.1 | `tabulator/tabulator.min.css`, `tabulator/tabulator.min.js` | MIT | https://github.com/olifolkerd/tabulator |

The corresponding original license texts are preserved at
`tabler/LICENSE` and `tabulator/LICENSE`.

Tabler is used **only for its core CSS controls and layout**. BondLens does
not bundle Tabler's optional chart or other third-party dependencies.
Tabulator renders the bond screener's data table; filtering, sorting, and
pagination are still implemented by the BondLens API.

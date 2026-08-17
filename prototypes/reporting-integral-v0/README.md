# Tablero Integral — V0 prototype

Standalone mockup, no backend/Graph/production data. Consolidates the 12
functional pages of the current "Danwor - Tablero Integral" Power BI report
into 6 candidate experiences, based on the PBIP metadata assessment in
`artifacts/reporting-integral-v0/`.

## Open it

Just open `index.html` directly in a browser (double-click, or `file://`).
No server, no build step, no dependencies.

## What it shows

- 6 top-level experiences (tabs) instead of 12 pages.
- Dual-perspective selectors ("Ver por") inside each experience, replacing
  duplicate pages (e.g. Cartera/Proyectos: Mantenimiento \| Proyecto).
- A shared filter bar (Estado is wired to actually filter the Cartera
  table; the rest are decorative placeholders for this V0).
- A "Ver mapeo 12 → 6" drawer showing which original pages fold into which
  experience.

Dataset is hardcoded and representative — not production data.

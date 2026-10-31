# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-10-31

### Portfolio workbench
- Five-page Chinese/English workbench: Market, Screener, Bond, Agent Research and History.
- Deterministic financial analysis with source/date lineage, clear data-mode boundaries, on-demand AI interpretation, and privacy-reduced replay/evidence outputs.
- Bounded 20-row server-side history pagination, disabled-history read/write boundary, mode-aware caveats and bilingual history status labels.
- Reproducible static case study for **21附息国债03**, with matching screenshots, HTML/JSON evidence, verification commands and a silent screenshot tour.
- Short bilingual portfolio READMEs and separate technical guides; current GitHub Pages presentation highlights the same case.

### Verification
- Local regression on 2026-10-31: **253 passed, 1 skipped**; GitHub CI, agent/red-team evaluations and Docker health checks passed on portfolio commit `b2a35d2`.
- Case values cross-checked between the workbench API and deterministic agent. The source is the **2024-03-22 historical static sample**, not a live market quote.

### Remaining boundaries
- First keyword search through thousands of old history JSON files can be slow (approximately 19.5 seconds in one isolated 3,300-record cold-search test). Initial history page uses cursor pagination.
- No built-in authentication or per-user history isolation. Duration/DV01 are teaching-assumption calculations and do not represent full fixed-income pricing.
- Repo maintenance after portfolio freeze is manual; unnecessary dependency-version PRs are not automatically created. Security alerts and compatibility updates still require review.

## [0.1.0] - 2026-07-30

### Added
- Evidence-first Chinese bond analysis agent with Flask + Jinja UI.
- Deterministic tool chain for market overview, search, ranking, outliers, monitor boards, and single-bond reports.
- Trust Layer with evidence quality, numeric/language guardrails, answer judge, risk profile, Trust score, Evidence Pack, and replay summaries.
- Bilingual UI with Chinese default and a single header `中 / EN` switch using query/cookie memory.
- Optional OpenAI-compatible LLM narration under strict guardrails; CPA path validated with `haochi/gpt-5.4`.
- Live/snapshot/static data modes with explicit data lineage and deterministic no-key fallback.
- Docker packaging, Compose demo, `/healthz` health check, and CI gates for ruff, pytest, agent evals, red-team evals, and Docker health.

### Safety
- Advisory/buy/guaranteed-return/risk-free prompts are policy-blocked before LLM use.
- Unsupported LLM numbers or unsafe risk language fail closed to deterministic output.

### Notes
- This is a portfolio/research demo, not an investment advisory system.

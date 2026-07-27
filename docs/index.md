# BondLens: An Evidence-First Bond Analysis Agent for Chinese Market Data

<div align="center">

<img src="figs/logo_white_background.png" width="42%" alt="BondLens logo"/>

**Numbers are code-calculated. Narratives are LLM-assisted. Every output is provenance-tracked.**

![CI](https://github.com/Phoenix0531-sudo/BondLens/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Flask](https://img.shields.io/badge/Flask-3.x-green.svg)
![Tests](https://img.shields.io/badge/tests-pytest%2Bevals-informational)
![i18n](https://img.shields.io/badge/i18n-zh%2Fen-teal)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

</div>

BondLens is a lightweight agent for **Chinese bond market analysis**.
It turns a natural-language question into an auditable run with live/snapshot/static data, deterministic tools, optional LLM narration under guardrails, and a reviewer-facing Trust Layer.

Unlike broad multi-agent equity research platforms, BondLens does not try to be a full investment desk.
Its design choice is narrower and more honest: **numbers come from code**, the model may only narrate over evidence, and every answer can be replayed, judged, and red-teamed.

## Quick links

- Repository: <https://github.com/Phoenix0531-sudo/BondLens>
- English README: <https://github.com/Phoenix0531-sudo/BondLens/blob/main/README.md>
- 中文 README: <https://github.com/Phoenix0531-sudo/BondLens/blob/main/README.zh-CN.md>
- Static demo packs (no API key): <https://github.com/Phoenix0531-sudo/BondLens/tree/main/docs/demo_runs>
- CI: <https://github.com/Phoenix0531-sudo/BondLens/actions/workflows/ci.yml>

## Local quick start

```bash
cp .env.example .env
docker compose up --build
# or: PORT=8765 BOND_DATA_MODE=auto python app.py
```

## License

MIT. Non-investment advice — learning, research, portfolio demonstration, and interview discussion only.

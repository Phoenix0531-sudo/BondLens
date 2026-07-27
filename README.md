# BondLens AI

**An evidence-first bond analysis agent for Chinese market data**

[English](README.md) | [中文](README.zh-CN.md)

![CI](https://github.com/Phoenix0531-sudo/BondLens/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

```text
Numbers are code-calculated.
Narratives are LLM-assisted.
Every output is provenance-tracked.
```

BondLens is a lightweight AI agent platform for **Chinese bond market analysis**.
It unifies deterministic analytics, optional LLM narration, and a reviewer-facing Trust Layer —
so every number can be audited, every answer can be replayed, and every limitation is stated honestly.

> Non-investment advice. For learning, research, portfolio demonstration, and interview discussion only.

Project page: [https://phoenix0531-sudo.github.io/BondLens/](https://phoenix0531-sudo.github.io/BondLens/)

Static demo packs (no API key): [docs/demo_runs/](docs/demo_runs/)

---

## Design Principle: Deterministic Compute, LLM Narration

A core design principle of BondLens (shared with platforms such as FinRobot) is the strict separation between **deterministic financial computation** and **LLM-based narration**.

| Layer | What produces it | Can invent numbers? |
| --- | --- | --- |
| Yield / volume / percentiles / rankings | Deterministic tools (`bond_agent/tools.py`) | No |
| Taxonomy / maturity buckets / peer spread | Rule-based classifiers + pure Python stats | No |
| Data lineage (live / snapshot / static) | Data resolver | No |
| Evidence ledger claims | Built from tool outputs | No |
| Final narrative text | Deterministic report, or LLM only if guardrail + judge pass | Text only; numbers must match evidence |

In short: tools compute, models narrate, trust decides.

---

## What BondLens Does

BondLens turns a natural-language bond question into an **auditable analysis run**:

1. Resolve data (live AkShare → cached snapshot → local Excel)
2. Plan intent (overview / search / ranking / outliers / monitor / composite / bond report)
3. Run deterministic tools
4. Build structured evidence (market, peer, monitor, quality, maturity coverage)
5. Compose a report with risk notes and mandatory limitations
6. Optionally polish with an LLM under numeric + language guardrails
7. Score trust, export a Bond Evidence Pack, and store a replay summary

### Product surfaces (answer-first)

- **Answer Snapshot**: 3-sentence headline + key metrics; full body collapsed by default
- **SSE stream + soft final render**: tool-step progress, token preview, final summary card without forced full-page reload; share/full board still via `result_url`
- **Bilingual UI (zh default)**: query/cookie language memory, explicit zh/en switch, bilingual provenance lines
- **Bond type mix + maturity buckets**: conservative name-rule taxonomy (no rating inference)
- **Peer comparison**: same type + maturity bucket spread vs peers
- **Cross-section monitor board**: high yield / low volume / yield outliers / missing maturity
- **Maturity / residual board**: live coverage, cashflow teaching duration/DV01, perpetual dual scenarios (first finite leg + theoretical consol)
- **Trust score + stress view + audit folds**: guardrail / judge / risk / ledger behind details

---

## Architecture

```text
Data Ops      live / snapshot / static sample + lineage + maturity enrichment
Agent Core    Planner → Tools → Evidence → Report
Trust Layer   Guardrail + Judge + Risk Profile + Trust Score + Replay + Evals
```

```mermaid
flowchart TD
    A[User Question] --> B[Data Source Resolver]
    B --> C[Planner multi-intent]
    C --> D[Deterministic Tools]
    D --> E[Structured Evidence]
    E --> F[Report + Limitations]
    F --> G{Optional LLM}
    G -->|guardrail pass| H[Narrated answer]
    G -->|fail or disabled| I[Deterministic answer]
    H --> J[Judge + Trust + Pack + Replay]
    I --> J
```

Inspired by *deterministic compute, LLM narration* research platforms, BondLens specializes the idea for **Chinese bonds** with claim-level evidence, answer judging, red-team evals, and reviewer-facing evidence packs — not a multi-role equity research desktop.

---

## Screenshots

> UI screenshots will be recaptured after the product surface is fully stabilized.
> Current images under `docs/screenshots/` reflect an earlier workbench revision.

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/agent-workbench.png" alt="BondLens AI agent workbench">
      <br><strong>Agent Workbench</strong>
    </td>
    <td width="50%">
      <img src="docs/screenshots/agent-answer-evidence.png" alt="Agent answer and evidence view">
      <br><strong>Answer, Tool Trace, and Evidence</strong>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/risk-profile-judge.png" alt="Risk profile and answer judge">
      <br><strong>Risk Profile and Answer Judge</strong>
    </td>
    <td width="50%">
      <img src="docs/screenshots/replay-dashboard.png" alt="Agent replay dashboard">
      <br><strong>Replay Dashboard</strong>
    </td>
  </tr>
</table>

---

## Quick Start

### 5 minutes (offline demo)

```bash
pip install -r requirements.txt
# preferred local demo bind
export FLASK_RUN_HOST=127.0.0.1
export PORT=8765
export BOND_DATA_MODE=static
export SECRET_KEY=local-dev
python app.py
# open http://127.0.0.1:8765/agent
# try: 当前样本收益率分布是什么样？
```

Or open a pre-generated pack with no server:

- [demo-market-overview.html](docs/demo_runs/demo-market-overview.html)
- [demo-bond-report.html](docs/demo_runs/demo-bond-report.html)
- [demo-yield-outliers.html](docs/demo_runs/demo-yield-outliers.html)

### 30 minutes (live path + fallback)

```bash
export FLASK_RUN_HOST=127.0.0.1
export PORT=8765
export BOND_DATA_MODE=auto   # live first, then snapshot, then static
python app.py
# force live: BOND_DATA_MODE=live
# watch data_source.runtime_mode, maturity board, and trust score when live degrades
```

Optional LLM polish (never required):

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=http://127.0.0.1:31876/v1   # example: local OpenAI-compatible gateway
export OPENAI_MODEL=deepseek-v4-flash-search   # verified on this host; gpt/grok often unavailable
export OPENAI_API_STYLE=chat
export OPENAI_MODEL_FALLBACKS=gpt-5.4-mini,grok-4.5   # optional; /models probe reorders live ids
# Keys stay in process env only. Do not commit secrets.
```

---

## Language (i18n)

- Default UI language: **Chinese**
- Explicit switch in the page header (中 / EN)
- Persistence: `?lang=zh|en` query wins, then `bondlens_lang` cookie, else `zh`
- Covers templates, intent/tool labels, deterministic report skeleton, advisory refusal, and flash/error copy
- LLM system prompts follow the active language; logs stay developer-facing and are not fully bilingual

## Tool Catalog (deterministic operators)

| Tool | Inputs | Deterministic outputs |
| --- | --- | --- |
| `search_bonds` | name / type / maturity / yield filters | match_count, records |
| `describe_market` | active frame | yield/volume summaries, segments, data quality |
| `rank_bonds` | by, top_n, order | ranked records |
| `detect_yield_outliers` | method, threshold | outlier_count, scores |
| `compare_bond_to_market` | bond / record | percentiles, peer comparison |
| `build_market_monitor` | top_n | high-yield / low-volume / outliers / missing maturity |
| `generate_bond_report` | tool outputs + plan | analysis, risk notes, limitations |

Numbers in the final answer must come from these tools (or be rejected by the guardrail).

---

## Trust Score & Evidence Pack

Each answer includes `trust_score` (0–100) built from evidence quality, data freshness/degradation,
ledger coverage, guardrail outcome, judge outcome, and a forced non-advisory penalty.

Every run can export a portable **Bond Evidence Pack** (JSON + static HTML):

- question / intent / tools
- data lineage + maturity coverage
- trust score + adjustments
- guardrail + judge + risk profile
- evidence ledger + final answer
- mandatory limitations

```bash
python scripts/generate_demo_packs.py
```

Runtime packs: `.tmp/evidence_packs/`  
Committed demos: [docs/demo_runs/](docs/demo_runs/)

### Maturity unmatched export

Live/snapshot feeds have **no native maturity field**. BondLens enriches matched names from the local security master and exposes:

```text
GET/POST /api/maturity/unmatched?format=csv&data_mode=static
GET/POST /api/maturity/unmatched?format=json&data_mode=static
```

The UI maturity board links to the same export for unmatched bond names.

---

## Example Questions

```text
当前样本收益率分布是什么样？
搜索23附息国债26并给出收益率分析
打开今日市场监控面板：高收益、低成交与异常
按收益率列出最高的前5只债券
有没有收益率异常的债券？
筛选国债收益率大于 2.5 的债券
```

---

## API

```http
POST /api/agent/query
Content-Type: application/json

{
  "question": "搜索23附息国债26并给出收益率分析",
  "data_mode": "auto"
}
```

Streaming (SSE):

```http
POST /api/agent/stream
Content-Type: application/json

{
  "question": "当前债券市场样本概览如何？",
  "data_mode": "static"
}
```

Events include `status` (tool steps), `token` (partial text), and `final` (soft-render view + `result_url`).

Operational endpoints:

```text
GET  /healthz
GET  /api/agent/schema
GET  /replay
GET  /packs/<pack_id>.html
GET  /packs/<pack_id>.json
GET  /api/maturity/unmatched
```

Deployment notes: [docs/deployment.md](docs/deployment.md)

---

## Data Source Boundary

```text
Primary:        ChinaMoney / AkShare-style spot deal fetch (direct preferred)
Snapshot:       .tmp/bond_spot_deal_snapshot.csv
Final fallback: data/testdata.xlsx
```

- Live fields used include bond name, clean price, yield, BP change, weighted yield, volume, and native residual maturity when present (`termToMaturity`)
- Residual maturity may still be incomplete → coverage board + trust penalty on weak coverage
- Cashflow duration / DV01 are **teaching-level** level-coupon estimates, not OAS / full call-tree valuation
- Perpetual-style residuals expose dual scenarios (first finite leg + theoretical perpetual), not a multi-century fake tenor
- No issuer ratings, financial statements, guarantees, or credit events
- Yield is a **risk signal**, not a trade instruction

Modes:

```text
auto   -> live first, cached snapshot second, local fallback third
live   -> live source requested; fallback reason shown if it degrades
static -> local Excel only
```

---

## Why This Is An Agent, Not A Chatbot

1. **Data resolver** chooses live / snapshot / static with honest lineage
2. **Planner** classifies multi-intent and selects tools
3. **Tools** run pure Python analytics over the active frame
4. **Evidence** is structured and ledger-backed
5. **Report** is composed from evidence with risks and limitations
6. **Optional LLM** may narrate only after local evidence exists
7. **Guardrail + judge** accept or reject model text
8. **Trust score + Evidence Pack + replay** make the run reviewable without dumping raw JSON

If `OPENAI_API_KEY` is not set, the project still runs with deterministic fallback output.

---

## Appendix: LLM final-answer matrix (recorded)

### Current working path (2026-07)

Recorded against local **new-api** (`http://127.0.0.1:31876/v1`) with model
**`deepseek-v4-flash-search`**, `BOND_DATA_MODE=static`, `temperature=0`, one-shot
numeric repair when guardrail fails.
Stable first bond for report questions: **06国开24** (bond-name ascending, mergesort).

Full table: [docs/demo_runs/llm_matrix_deepseek_v4.md](docs/demo_runs/llm_matrix_deepseek_v4.md)
· raw: [llm_matrix_deepseek_v4.json](docs/demo_runs/llm_matrix_deepseek_v4.json)

| Scenario | Lang | Threshold | Result | Notes |
| --- | --- | --- | --- | --- |
| overview | zh | 3/3 final LLM | **3/3** | direct pass |
| bond report | zh | 3/3 final LLM | **3/3** | direct pass |
| overview | en | >=2/3 | **2/3** | 1 residual invented `5%`; repair may recover |
| bond report | en | >=2/3 | **3/3** | residual duration%/percentile invents caught by guardrail + repair |

Historical `grok-4.5` matrix (same thresholds, earlier channel):
[docs/demo_runs/llm_matrix_grok45.md](docs/demo_runs/llm_matrix_grok45.md).
On this host, `gpt-5.4*` / `grok-4.5` channels are often unavailable or timeout;
**deepseek-v4-flash-search** is the currently verified chat model.

Honest residuals:

- Provider channel churn still forces deterministic fallback when models vanish
- Guardrails stay on; unsupported numbers never become final
- English overviews may still invent bare shares like `5%` under channel noise; prompt + `market_focus_numbers` reduce this, guardrail still fails closed
- One-shot repair rewrites only after a failed numeric check; not a free pass
- End-to-end latency often 8–25s on deepseek; repair path can exceed 40s
- Soft-render shows a summary card; full dashboard tables remain on `result_url`
- Not implemented: WebSocket tick feed, true OAS / full call-tree perpetual pricing, desktop GUI/CLI

This matrix is evidence of a working path, **not** a zero-bug claim.

---

## Background

This project started as a 2024 undergraduate thesis: a Flask-based bond data analysis system.
The original thesis version is preserved and should not be rewritten:

- Original thesis branch: `undergraduate-thesis-2024`
- Current branch: `main`

## License

MIT

## Disclaimer

BondLens AI is an engineering and research demonstration. It does not provide investment advice,
does not claim complete market coverage, and does not replace professional fixed-income research tools.

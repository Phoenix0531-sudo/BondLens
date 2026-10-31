# BondLens technical guide

[Project overview](../README.md) · [中文指南](TECHNICAL_GUIDE.zh-CN.md)

This guide describes the local workbench at the V1.1 snapshot. It is a single-instance research demonstration, not a public multi-user or trading service. For a concise product tour and local setup, start with the [README](../README.md).

## Reproducible portfolio case

The repository includes a [single-bond case study for 21附息国债03](portfolio_case/CASE_STUDY.md), a [rendered HTML evidence pack](portfolio_case/bond-study.html), and [verifiable calculation data](portfolio_case/case-data.json), all based on the **March 22, 2024 static sample**. Its target yield is 2.68%, with a +7.23 bp spread over 19 comparable records sharing bond type and maturity bucket. Per-row quote dates are absent, so same-day comparability cannot be independently verified.

```bash
python scripts/generate_portfolio_case.py          # Run deterministic tools in isolation and build the case
python scripts/generate_portfolio_case.py --check  # Recalculate and compare files without rewriting
node scripts/capture_portfolio_case.mjs            # Optional: recapture real UI screens in isolated Edge
python scripts/build_portfolio_walkthrough.py     # Optional: create a 60s static tour with FFmpeg
```

No real-time feed or model provider is required. The evidence pack uses a fixed build timestamp for reproducible output: **it is metadata, not a quote date or the actual run time**. Screenshot result IDs and local history times may vary between captures. The optional video is a tour assembled from real screenshots, not a recording of interactive clicks; none of these artifacts is a hosted interactive instance.

## Local operation and configuration

Python 3.12 is the deployment baseline (3.11+ supported). Install runtime dependencies from `requirements.lock`; add `requirements-dev.txt` for testing. `python app.py` defaults to `127.0.0.1:5000`; `PORT` and `FLASK_RUN_HOST` can override those values. For an offline session, set `BOND_DATA_MODE=static` before starting. Direct Python execution reads process environment and does not automatically load `.env`; see [the example configuration](../.env.example) for available options.

The development server is for local use. There is no built-in authentication or per-user history isolation. A network-facing setup needs a deliberately configured authenticated reverse proxy; changing the bind address alone is not a security solution.

### Docker

```bash
docker compose up --build
# http://127.0.0.1:8765/ and http://127.0.0.1:8765/healthz
```

The `bondlens` service defaults to `${BOND_BIND_HOST:-127.0.0.1}:${BOND_HOST_PORT:-8765}:5000`. It uses one Gunicorn process with four threads; result URLs depend on an in-process cache, so additional workers change that behavior. `docker compose down` stops the stack without deleting its volume. History and packs live in the `bondlens-data` volume when their configured paths remain under `/app/.tmp`; back up the volume if needed. `docker compose down -v` deletes it.

Optional model configuration uses environment variables such as `OPENAI_BASE_URL`, `OPENAI_MODEL` and `OPENAI_API_STYLE`; do not commit credentials. Docker can reach a host Ollama instance through `http://host.docker.internal:11434/v1` where supported. A configured model is not called automatically.

### Checks

```bash
python -m pip install -r requirements.lock -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
node --check static/js/replay.js
node scripts/browser_smoke.mjs
```

The browser smoke script uses an isolated local server and temporary history. Its Windows Edge and Python executable defaults are configurable via `BOND_BROWSER_EDGE` and `BOND_BROWSER_PYTHON`; it requires Node with WebSocket support. On 2026-10-31, the independent local result was 252 passed and 1 skipped, and CI succeeded for `595b87e`. These are historical results, not a guarantee of current CI or real-provider behavior.

## Data modes and interpretation

- **Live:** real-time feed only; failure returns an error, without snapshot or static fallback.
- **Auto:** try live, then a valid cached snapshot, then the local sample. Consult `data_source` for the actual source, as-of and fallback reason.
- **Static:** use the local `data/testdata.xlsx` sample without contacting live feeds. The documented screenshot sample date is 2024-03-22.
- **Import:** an uploaded dataset takes precedence for that session. It is held in memory and expires; upload again when its token expires.

Live inputs include bond name, clean price, yield, BP change, weighted yield, volume and residual maturity when provided. Maturity may be missing; the coverage board and unmatched export make gaps visible. Do not assume every feed has a native maturity value. Peer comparison uses bond type and maturity bucket, and quote date when available. Without a date column, same-day alignment cannot be established; a missing target date is not compared against other dates.

Duration and DV01 use teaching-level cash-flow assumptions; perpetual-style tenors include first finite-leg and theoretical perpetual scenarios, not a full call-tree or OAS model. Yield outliers describe statistical deviations, not credit, default or investment conclusions. Issuer ratings, financial statements, guarantees and credit events are outside the feed. Check data-provider redistribution rights separately from the software license.

## Seven deterministic tools

A rule-based planner selects from these operators; generated research can be used without an LLM.

| Tool | Purpose |
| --- | --- |
| `search_bonds` | Filter by name, type, maturity or yield. |
| `describe_market` | Summarize yield, volume, segments and data quality. |
| `rank_bonds` | Rank records by a supported numeric field. |
| `detect_yield_outliers` | Report grouped statistical deviations and method. |
| `compare_bond_to_market` | Compare a bond with the market and eligible peers. |
| `build_market_monitor` | Summarize high yield, low volume, outliers and missing maturity. |
| `generate_bond_report` | Assemble analysis, risk notes and limitations from outputs. |

## Import and safety

`POST /api/import` accepts multipart `file` uploads in CSV, XLSX or XLS format. Requests are limited to 1 MiB. The import report describes mappings, missing fields, duplicate names and date coverage; missing inputs disable dependent analysis rather than being invented. Files with multiple quote dates are marked as mixed timepoints. The returned `import_id` selects the in-memory dataset for later requests. The session cache is limited to eight imports and has a one-hour TTL; a restart or expiry requires a new upload.

Use the UI mapping flow, or pass a `column_overrides` JSON object mapping target fields to source columns. Auto-mapping uses compatible field meanings; dirty/full price is not clean price, and turnover amount is not traded volume. Explicit overrides for those incompatible mappings are rejected. Uploads are temporarily written under `BOND_UPLOAD_DIR` (default `.tmp/uploads`) for parsing and normally removed afterwards; interrupted processes may leave temporary files. The attachment is not retained as a historical artifact; only privacy-reduced derived output may be saved. Do not enter secrets or personal information into questions or bond names: redaction is best-effort.

## History and evidence packs

`BOND_REPLAY_DIR` defaults to `.tmp/replays`, and `BOND_EVIDENCE_PACK_DIR` to `.tmp/evidence_packs`. `BOND_REPLAY_ENABLED=false` disables history reads and writes without deleting old files. Evidence-pack serving is a separate boundary; disabling history does not itself block existing `/packs/...` files. The evidence-pack generation setting is `BOND_EVIDENCE_PACK_ENABLED`.

Versioned history stores a sanitized derived result up to 2 MiB, not the original upload or entire table. Some imported free text is omitted and cannot be restored. A restored result is read-only; a re-run produces a separate record. Legacy summary-only entries cannot recreate the full result. Records and HTML/JSON packs are individually written atomically, not as a cross-file transaction; interruptions can leave orphaned or missing artifacts. Missing packs are not advertised as available.

The history page uses 20-record server-side cursor pages. Filename ordering is used for paging. Searching and metadata filters scan records; in an isolated local test, a cold search for the oldest match among 3,300 roughly 16 KiB files took about 19.5 seconds. Use separate history and pack directories for demonstrations and back up archives; do not delete existing history to work around the limitation.

Evidence Packs carry selected question/intent, tool and lineage information, trust and risk context, evidence, answer and limitations. [Static demo packs](demo_runs/) are provided. To generate demo packs intentionally, run `python scripts/generate_demo_packs.py`; it writes output, so choose an isolated environment when testing.

## HTTP and streaming

```http
POST /api/agent/query
Content-Type: application/json

{"question":"当前债券市场样本概览如何？","data_mode":"static"}
```

`POST /api/agent/stream` accepts the same shape and emits SSE `status`, `token`, `final` or `error` events. Ordinary analysis does not request a model. A user can explicitly request interpretation through `/api/agent/explain`; imported data requires confirmation before sending. Successful interpretations can be cached, while a user-initiated retry after failure may call a configured provider again. Stopping browser receipt does not guarantee server or provider cancellation. Request questions are limited to 4,000 characters.

Other endpoints:

```text
GET  /healthz                     GET  /api/agent/schema
POST /api/import                 GET  /api/data/source
GET  /api/market/overview        GET  /api/bonds/search
GET  /api/bond/detail?key=...    GET  /replay
GET  /replay/<id>                GET  /packs/<id>.html|json
GET/POST /api/maturity/unmatched?format=csv|json
```

Workbench reads accept `data_mode`, `import_id` and `lang` as appropriate. Client-supplied directories are not used for pack downloads. Query `/api/agent/schema` for the current request and response contracts.

## Language, examples and provenance

The header switches Chinese and English. `?lang=zh|en` takes precedence over the language cookie; Chinese is the default. UI labels and deterministic report skeletons are bilingual; developer logs are not guaranteed to be fully translated.

- [Market HTML](demo_runs/demo-market-overview.html), [bond HTML](demo_runs/demo-bond-report.html), [outliers HTML](demo_runs/demo-yield-outliers.html): pre-generated static reports, not an interactive service; JSON siblings are in the same directory.
- [Recorded LLM matrix](demo_runs/llm_matrix_cpa_gpt54.md): a historical provider-path observation, not a production guarantee or current real-provider test.
- [GitHub Pages](https://phoenix0531-sudo.github.io/BondLens/) is a static project page, not a hosted interactive workbench.
- The original 2024 undergraduate thesis version is preserved on branch `undergraduate-thesis-2024`; current work continues on `main`.

[MIT license](../LICENSE) · Research and education only; not investment advice.

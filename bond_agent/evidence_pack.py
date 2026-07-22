"""Bond Evidence Pack: export one agent run as auditable JSON + static HTML."""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data_loader import PROJECT_ROOT

DEFAULT_PACK_DIR = PROJECT_ROOT / ".tmp" / "evidence_packs"
DEMO_PACK_DIR = PROJECT_ROOT / "docs" / "demo_runs"
LIMITATIONS_TEMPLATE_ZH = [
    "非投资建议，仅用于学习和研究。",
    "当前行情源不包含主体评级、财务报表、担保与信用事件。",
    "收益率高低是风险信号，不是买卖依据。",
    "实时链路可能降级到快照或本地样本，请以 data_source 血缘为准。",
]
LIMITATIONS_TEMPLATE_EN = [
    "Non-investment advice. For learning and research only.",
    "The market feed does not include issuer ratings, financial statements, guarantees, or credit events.",
    "Yield level is a risk signal, not a buy/sell instruction.",
    "Live access may degrade to snapshot or local sample; trust the data_source lineage.",
]


def build_evidence_pack(response: dict[str, Any], *, pack_id: str | None = None) -> dict[str, Any]:
    """Build a compact, portable evidence pack from a full agent response."""
    created_at = datetime.now(timezone.utc).isoformat()
    pack_id = pack_id or response.get("replay_id") or _new_pack_id()
    data_source = response.get("data_source") or {}
    trust = response.get("trust_score") or {}
    limitations = list(response.get("limitations") or [])
    for item in LIMITATIONS_TEMPLATE_ZH:
        if item not in limitations:
            limitations.append(item)

    pack = {
        "schema_version": "1.0",
        "pack_type": "bond_evidence_pack",
        "id": pack_id,
        "created_at": created_at,
        "agent": response.get("agent") or "BondLens AI",
        "question": response.get("question"),
        "intent": (response.get("plan") or {}).get("intent"),
        "tools_used": response.get("tools_used") or [],
        "tool_trace": response.get("tool_trace") or [],
        "data_lineage": {
            "source_id": data_source.get("source_id"),
            "source_name": data_source.get("source_name"),
            "runtime_mode": data_source.get("runtime_mode"),
            "requested_mode": data_source.get("requested_mode"),
            "provider": data_source.get("provider"),
            "fetched_at": data_source.get("fetched_at"),
            "fallback_reason": data_source.get("fallback_reason"),
            "row_count": data_source.get("row_count"),
            "valid_yield_count": data_source.get("valid_yield_count"),
            "storage": data_source.get("storage"),
            "active_live_feed": data_source.get("active_live_feed"),
            "active_live_snapshot": data_source.get("active_live_snapshot"),
        },
        "trust_score": trust,
        "evidence_quality": response.get("evidence_quality") or {},
        "llm_guardrail": response.get("llm_guardrail") or {},
        "answer_judge": response.get("answer_judge") or {},
        "risk_profile": response.get("risk_profile") or {},
        "evidence_ledger": response.get("evidence_ledger") or [],
        "final_answer": response.get("final_answer"),
        "final_answer_source": response.get("final_answer_source"),
        "analysis": response.get("analysis") or [],
        "risk_notes": response.get("risk_notes") or [],
        "limitations": limitations,
        "limitations_template_en": LIMITATIONS_TEMPLATE_EN,
        "disclaimer": response.get("disclaimer") or LIMITATIONS_TEMPLATE_ZH[0],
        "replay_id": response.get("replay_id"),
    }
    return pack


def export_evidence_pack(
    response: dict[str, Any],
    *,
    pack_id: str | None = None,
    directory: str | Path | None = None,
    write_html: bool = True,
) -> dict[str, Any]:
    """Write JSON (+ optional HTML) evidence pack and return metadata paths."""
    pack = build_evidence_pack(response, pack_id=pack_id)
    out_dir = Path(directory) if directory else _pack_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(pack["id"])
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    html_path = None
    if write_html:
        html_path = out_dir / f"{stem}.html"
        html_path.write_text(render_evidence_pack_html(pack), encoding="utf-8")

    return {
        "id": pack["id"],
        "json_path": str(json_path),
        "html_path": str(html_path) if html_path else None,
        "pack": pack,
    }


def render_evidence_pack_html(pack: dict[str, Any]) -> str:
    """Render a self-contained static HTML evidence pack (no backend required)."""
    trust = pack.get("trust_score") or {}
    judge = pack.get("answer_judge") or {}
    guardrail = pack.get("llm_guardrail") or {}
    risk = pack.get("risk_profile") or {}
    lineage = pack.get("data_lineage") or {}
    ledger = pack.get("evidence_ledger") or []
    adjustments = trust.get("headline_reasons") or trust.get("adjustments") or []

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    def list_html(items: list[Any]) -> str:
        if not items:
            return "<p class='muted'>（无）</p>"
        return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ul>"

    ledger_html = []
    for item in ledger:
        ledger_html.append(
            f"""
            <article class="card">
              <div class="badge">{esc(item.get('confidence'))}</div>
              <h3>{esc(item.get('claim_zh') or item.get('claim_en'))}</h3>
              <p>{esc(item.get('evidence_zh') or item.get('evidence_en'))}</p>
              <p class="muted">tool={esc(item.get('tool'))} · source={esc(item.get('source'))}</p>
            </article>
            """
        )

    adj_html = []
    for item in adjustments:
        delta = item.get("delta", 0)
        sign = f"+{delta}" if int(delta) > 0 else str(delta)
        adj_html.append(
            f"<li><strong>{esc(sign)}</strong> {esc(item.get('reason_zh') or item.get('reason_en'))}</li>"
        )

    trust_level = esc(trust.get("level") or "n/a")
    trust_score = esc(trust.get("score") if trust.get("score") is not None else "n/a")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bond Evidence Pack · {esc(pack.get('id'))}</title>
  <style>
    :root {{
      --ink: #17211d;
      --muted: #62706b;
      --line: #d8ded9;
      --paper: #f7f5f0;
      --surface: #fffdf8;
      --teal: #0f6b5f;
      --amber: #c07a22;
      --red: #a9423a;
      --green: #2f7d55;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    }}
    .wrap {{ width: min(980px, calc(100% - 32px)); margin: 28px auto 56px; }}
    .hero {{
      padding: 28px;
      border: 1px solid var(--line);
      background: var(--surface);
      box-shadow: 0 18px 45px rgba(23,33,29,.08);
    }}
    .eyebrow {{ color: var(--teal); font-size: 12px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 8px 0 0; font-family: Georgia, "Times New Roman", serif; font-size: 40px; font-weight: 500; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    h3 {{ margin: 0 0 8px; font-size: 16px; }}
    p, li {{ line-height: 1.6; }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    .trust {{
      display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
      margin-top: 16px;
    }}
    .trust-score {{
      font-size: 34px; font-weight: 780; color: var(--teal);
    }}
    .badge {{
      display: inline-block; padding: 5px 9px; border: 1px solid var(--line);
      background: #f4f0e7; color: var(--muted); font-size: 12px; margin-right: 6px;
    }}
    .badge.good {{ color: var(--green); border-color: rgba(47,125,85,.35); }}
    .badge.warn {{ color: var(--amber); border-color: rgba(192,122,34,.35); }}
    .badge.bad {{ color: var(--red); border-color: rgba(169,66,58,.35); }}
    .grid {{ display: grid; gap: 14px; margin-top: 16px; }}
    .section {{
      padding: 18px 20px; border: 1px solid var(--line); background: var(--surface);
    }}
    .cards {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }}
    .card {{ padding: 14px; border: 1px solid var(--line); background: #f9f6ef; }}
    pre {{
      white-space: pre-wrap; word-break: break-word; background: #f3f0e8;
      padding: 14px; border-left: 3px solid var(--teal); font-size: 13px; line-height: 1.55;
    }}
    .footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <p class="eyebrow">Bond Evidence Pack</p>
      <h1>{esc(pack.get('question') or 'Untitled run')}</h1>
      <p class="muted">id={esc(pack.get('id'))} · created={esc(pack.get('created_at'))} · intent={esc(pack.get('intent'))}</p>
      <div class="trust">
        <div class="trust-score">{trust_score}<span style="font-size:16px">/100</span></div>
        <span class="badge {'good' if trust.get('level')=='high' else 'warn' if trust.get('level')=='medium' else 'bad'}">trust {trust_level}</span>
        <span class="badge">judge {esc(judge.get('status'))}</span>
        <span class="badge">guardrail {esc(guardrail.get('status'))}</span>
        <span class="badge">final {esc(pack.get('final_answer_source'))}</span>
        <span class="badge">data {esc(lineage.get('runtime_mode'))}</span>
      </div>
      <p class="muted" style="margin-top:12px">{esc(trust.get('summary_zh') or trust.get('summary_en') or '')}</p>
    </header>

    <div class="grid">
      <section class="section">
        <h2>数据血缘 · Data lineage</h2>
        <p><strong>{esc(lineage.get('source_name'))}</strong> · {esc(lineage.get('runtime_mode'))}</p>
        <p class="muted">provider={esc(lineage.get('provider'))} · rows={esc(lineage.get('row_count'))} · valid_yield={esc(lineage.get('valid_yield_count'))}</p>
        <p class="muted">fetched_at={esc(lineage.get('fetched_at'))}</p>
        <p class="muted">fallback={esc(lineage.get('fallback_reason') or 'none')}</p>
      </section>

      <section class="section">
        <h2>信任分调整 · Trust adjustments</h2>
        <ul>{''.join(adj_html) if adj_html else '<li class="muted">（无显著调整）</li>'}</ul>
      </section>

      <section class="section">
        <h2>答案评审 · Answer judge</h2>
        <p>{esc(judge.get('verdict_zh') or judge.get('verdict_en') or '')}</p>
        <p class="muted">score={esc(judge.get('score'))} · action={esc(judge.get('recommended_action'))}</p>
      </section>

      <section class="section">
        <h2>LLM 护栏 · Guardrail</h2>
        <p>{esc(guardrail.get('summary') or '')}</p>
        <p class="muted">status={esc(guardrail.get('status'))} · numeric={esc(guardrail.get('numeric_status'))} · language={esc(guardrail.get('language_status'))}</p>
      </section>

      <section class="section">
        <h2>风险画像 · Risk profile</h2>
        <p>{esc(risk.get('summary_zh') or risk.get('summary_en') or '')}</p>
        <p class="muted">overall={esc(risk.get('overall_level'))}</p>
      </section>

      <section class="section">
        <h2>工具轨迹 · Tool trace</h2>
        {list_html(pack.get('tool_trace') or [])}
      </section>

      <section class="section">
        <h2>证据账本 · Evidence ledger</h2>
        <div class="cards">{''.join(ledger_html) if ledger_html else '<p class="muted">（无 claim）</p>'}</div>
      </section>

      <section class="section">
        <h2>最终回答 · Final answer</h2>
        <pre>{esc(pack.get('final_answer'))}</pre>
      </section>

      <section class="section">
        <h2>分析 · Analysis</h2>
        {list_html(pack.get('analysis') or [])}
      </section>

      <section class="section">
        <h2>风险提示 · Risk notes</h2>
        {list_html(pack.get('risk_notes') or [])}
      </section>

      <section class="section">
        <h2>局限性 · Limitations（强制）</h2>
        {list_html(pack.get('limitations') or [])}
        <p class="muted" style="margin-top:12px">{esc(pack.get('disclaimer'))}</p>
      </section>
    </div>

    <p class="footer">BondLens AI · Bond Evidence Pack schema v{esc(pack.get('schema_version'))} · Numbers come from deterministic tools; LLM may only narrate over evidence.</p>
  </div>
</body>
</html>
"""


def _pack_dir() -> Path:
    configured = os.environ.get("BOND_EVIDENCE_PACK_DIR")
    return Path(configured) if configured else DEFAULT_PACK_DIR


def _new_pack_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"pack-{stamp}"


def _safe_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)
    return cleaned.strip("-") or "evidence-pack"

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BOND_DEMO_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
DATA_MODE = os.environ.get("BOND_DEMO_DATA_MODE", "static")
TIMEOUT_SECONDS = float(os.environ.get("BOND_DEMO_TIMEOUT_SECONDS", "30"))

CASES = [
    ("overview", "当前债券市场样本概览如何？", "market_overview", None),
    ("ranking", "收益率最高的债券是哪只？", "ranking", None),
    ("advisory", "今天该不该买债？", "advisory_refusal", "advisory_policy_block"),
]


def request_json(path: str, payload: dict | None = None) -> tuple[int, dict | str]:
    url = f"{BASE_URL}{path}"
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            if response.headers.get("Content-Type", "").startswith("application/json"):
                return response.status, json.loads(raw)
            return response.status, raw
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def main() -> int:
    print(f"BondLens demo smoke: {BASE_URL} data_mode={DATA_MODE}")
    status, health = request_json("/healthz")
    if status != 200 or not isinstance(health, dict) or health.get("service") != "BondLens":
        print(f"health failed: status={status} body={health!r}")
        return 1
    print("health OK")

    failures: list[str] = []
    for name, question, expected_intent, expected_llm_error in CASES:
        status, body = request_json(
            "/api/agent/query",
            {"question": question, "data_mode": DATA_MODE, "lang": "zh"},
        )
        if status != 200 or not isinstance(body, dict):
            failures.append(f"{name}: HTTP {status} non-json body")
            continue
        components = (body.get("trust_score") or {}).get("components") or {}
        intent = body.get("intent") or components.get("intent")
        final_answer = body.get("final_answer") or ""
        llm_error = body.get("llm_error")
        checks = [
            (intent == expected_intent, f"intent={intent!r}, expected {expected_intent!r}"),
            (bool(final_answer.strip()), "final_answer is empty"),
            (body.get("final_answer_source") in {"deterministic_fallback", "llm"}, "bad final_answer_source"),
        ]
        if expected_llm_error is not None:
            checks.append((llm_error == expected_llm_error, f"llm_error={llm_error!r}"))
            checks.append((body.get("used_llm_in_final") is False, "advisory used LLM final"))
        bad = [msg for ok, msg in checks if not ok]
        if bad:
            failures.append(f"{name}: " + "; ".join(bad))
        else:
            print(
                f"{name} OK: intent={intent} source={body.get('final_answer_source')} "
                f"llm={body.get('llm_status')} trust={(body.get('trust_score') or {}).get('score')}"
            )

    if failures:
        print("FAIL")
        for item in failures:
            print(f"- {item}")
        return 1
    print("demo smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

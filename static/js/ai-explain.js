/* On-demand AI interpretation: explicit user action only.
 *
 * Binds (via delegation) to any [data-ai-button] inside a [data-ai-explain]
 * container that also holds [data-ai-status] and [data-ai-body] elements.
 * One POST /api/agent/explain per click; the server enforces one in-flight
 * call per target (409 in_progress). Uploaded-data targets require an
 * explicit confirm() before anything leaves the browser. Failures render a
 * clear message; the deterministic result on the page is never touched.
 */
(function () {
  "use strict";

  function currentLang() {
    return (document.documentElement.dataset.initialLang || "zh").toLowerCase().startsWith("en")
      ? "en"
      : "zh";
  }

  function textFor(el, zh, en) {
    el.setAttribute("data-lang-zh", zh);
    el.setAttribute("data-lang-en", en);
    el.textContent = currentLang() === "en" ? en : zh;
  }

  function setStatus(container, zh, en, isError) {
    const status = container.querySelector("[data-ai-status]");
    if (!status) return;
    textFor(status, zh, en);
    status.classList.toggle("form-error", !!isError);
    status.hidden = false;
  }

  function renderBody(container, explanation) {
    const body = container.querySelector("[data-ai-body]");
    if (!body) return;
    const lang = currentLang();
    body.textContent = explanation.text || "";
    body.hidden = false;
    const meta = [];
    if (explanation.model) meta.push(explanation.model);
    if (explanation.guardrail_status) {
      meta.push(lang === "en" ? "guardrail passed" : "护栏通过");
    }
    if (meta.length) {
      setStatus(
        container,
        `AI 解读 · ${meta.join(" · ")} · 非投资建议`,
        `AI interpretation · ${meta.join(" · ")} · not investment advice`,
        false
      );
    }
  }

  async function requestExplanation(button) {
    const container = button.closest("[data-ai-explain]");
    if (!container) return;
    const lang = currentLang();
    const params = {
      result_id: button.dataset.resultId || "",
      code: button.dataset.code || "",
      name: button.dataset.name || "",
      bond_key: button.dataset.bondKey || "",
      data_mode: button.dataset.dataMode || "",
      import_id: button.dataset.importId || "",
    };
    const busyZh = "AI 解读生成中，请稍候…";
    const busyEn = "Generating AI interpretation…";
    const idleZh = button.dataset.langZh || "生成 AI 解读";
    const idleEn = button.dataset.langEn || "Generate AI interpretation";
    button.dataset.busy = "1";
    button.disabled = true;
    textFor(button, busyZh, busyEn);
    setStatus(
      container,
      "基于已计算的确定性结果调用一次模型；不重算数据。",
      "One model call over the already-computed deterministic result; no recomputation.",
      false
    );
    const call = (confirmSend) =>
      fetch("/api/agent/explain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ lang, confirm_send: confirmSend }, params)),
      });

    const handle = (payload) => {
      if (!payload || typeof payload !== "object") {
        setStatus(container, "AI 解读响应异常。", "Unexpected AI interpretation response.", true);
        return;
      }
      const message = lang === "en" ? payload.message_en || payload.error : payload.message_zh || payload.error;
      switch (payload.status) {
        case "success":
          renderBody(container, payload);
          button.hidden = true;
          break;
        case "in_progress":
          setStatus(
            container,
            "该目标的 AI 解读正在生成中，请稍候再试。",
            "An AI interpretation for this target is already in progress; retry shortly.",
            false
          );
          break;
        case "needs_confirmation":
          /* Handled below before we ever get here in the normal flow. */
          setStatus(
            container,
            message || "需要确认后才能发送给外部模型。",
            message || "Confirmation is required before sending to the external model.",
            false
          );
          break;
        default:
          setStatus(
            container,
            message || "AI 解读未能生成；确定性结果不受影响。",
            message || "AI interpretation could not be generated; the deterministic result is unaffected.",
            true
          );
          break;
      }
    };

    try {
      let response = await call(false);
      let payload = await response.json().catch(() => ({}));
      if (payload.status === "needs_confirmation") {
        const question =
          lang === "en"
            ? payload.message_en || "Send the structured summary of this study to the external model?"
            : payload.message_zh || "将该研究的结构化摘要发送给外部模型生成解读？";
        if (!window.confirm(question)) {
          setStatus(
            container,
            "已取消：未向外部模型发送任何数据。",
            "Cancelled: nothing was sent to the external model.",
            false
          );
          return;
        }
        response = await call(true);
        payload = await response.json().catch(() => ({}));
      }
      handle(payload);
    } catch (err) {
      setStatus(
        container,
        `AI 解读请求失败（${(err && err.message) || "network"}）；确定性结果不受影响。`,
        `AI interpretation request failed (${(err && err.message) || "network"}); the deterministic result is unaffected.`,
        true
      );
    } finally {
      button.dataset.busy = "0";
      if (!button.hidden) {
        button.disabled = false;
        textFor(button, idleZh, idleEn);
      }
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-ai-button]") : null;
    if (!button || button.disabled || button.dataset.busy === "1") return;
    requestExplanation(button);
  });
})();

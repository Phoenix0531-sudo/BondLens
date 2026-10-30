(function () {
    const form = document.getElementById("agent-query-form");
    const button = document.getElementById("run-agent-button");
    const note = document.getElementById("agent-wait-note");
    const progress = document.getElementById("agent-async-progress");
    const meta = document.getElementById("agent-async-meta");
    const title = document.getElementById("agent-async-title");
    const errorBox = document.getElementById("agent-async-error");
    const streamPreview = document.getElementById("agent-stream-preview");
    const streamActions = document.getElementById("agent-stream-actions");
    const cancelButton = document.getElementById("agent-cancel-button");
    const copyButton = document.getElementById("agent-copy-stream-button");
    const toolSteps = document.getElementById("agent-tool-steps");
    const softResult = document.getElementById("agent-soft-result");
    const softBadges = document.getElementById("agent-soft-badges");
    const softMeta = document.getElementById("agent-soft-meta");
    const softProvHeadline = document.getElementById("agent-soft-prov-headline");
    const softProvLines = document.getElementById("agent-soft-prov-lines");
    const softLineageHeadline = document.getElementById("agent-soft-lineage-headline");
    const softLineageLines = document.getElementById("agent-soft-lineage-lines");
    const softAnswer = document.getElementById("agent-soft-answer");
    const softOpenLink = document.getElementById("agent-soft-open-link");
    const softCopyAnswer = document.getElementById("agent-soft-copy-answer");
    const importFile = document.getElementById("import-file");
    const importButton = document.getElementById("import-upload-button");
    const importClearButton = document.getElementById("import-clear-button");
    const importIdInput = document.getElementById("import-id");
    const importStatus = document.getElementById("import-status");
    const dataModeSelect = document.getElementById("data_mode");
    if (!form || !button) {
      return;
    }

    let lastFinalPayload = null;
    let activeController = null;
    let timer = null;
    let streamChars = 0;
    let stepCount = 0;

    function currentLang() {
      return (document.documentElement.dataset.initialLang || "zh").toLowerCase().startsWith("en")
        ? "en"
        : "zh";
    }

    function clearToolSteps() {
      if (!toolSteps) return;
      toolSteps.innerHTML = "";
      stepCount = 0;
    }

    function markStepsDone() {
      if (!toolSteps) return;
      toolSteps.querySelectorAll("li").forEach((li) => {
        li.classList.remove("active");
        li.classList.add("done");
      });
    }

    function appendToolStep(message, stage, tool) {
      if (!toolSteps) return;
      toolSteps.querySelectorAll("li.active").forEach((li) => {
        li.classList.remove("active");
        li.classList.add("done");
      });
      stepCount += 1;
      const li = document.createElement("li");
      li.className = "active";
      const mark = document.createElement("span");
      mark.className = "step-mark";
      mark.textContent = String(stepCount);
      const body = document.createElement("span");
      const stageBit = stage ? `[${stage}] ` : "";
      const toolBit = tool ? `${tool}: ` : "";
      body.textContent = `${stageBit}${toolBit}${message || ""}`;
      li.appendChild(mark);
      li.appendChild(body);
      toolSteps.appendChild(li);
      toolSteps.scrollTop = toolSteps.scrollHeight;
    }

    function setBusy(isBusy) {
      const lang = currentLang();
      const busyText = lang === "en"
        ? (button.dataset.busyEn || "Analyzing, please wait…")
        : (button.dataset.busyZh || "分析中，请稍候…");
      const idleText = lang === "en"
        ? (button.dataset.langEn || "Run Agent")
        : (button.dataset.langZh || "运行 Agent");
      button.disabled = isBusy;
      button.textContent = isBusy ? busyText : idleText;
      button.setAttribute("aria-busy", isBusy ? "true" : "false");
      if (note) note.hidden = !isBusy;
      if (progress) progress.hidden = !isBusy;
      if (streamActions) streamActions.hidden = !isBusy;
      if (cancelButton) cancelButton.disabled = !isBusy;
      if (!isBusy) {
        if (errorBox) {
          errorBox.hidden = true;
          errorBox.textContent = "";
        }
        if (streamPreview) {
          streamPreview.hidden = true;
          streamPreview.textContent = "";
        }
        if (copyButton) copyButton.hidden = true;
        streamChars = 0;
      } else {
        clearToolSteps();
        if (softResult) softResult.hidden = true;
      }
    }

    function startTimer() {
      const started = Date.now();
      if (timer) clearInterval(timer);
      timer = setInterval(() => {
        if (!meta) return;
        const sec = Math.floor((Date.now() - started) / 1000);
        const streamHint = streamChars > 0
          ? (currentLang() === "en" ? ` · ${streamChars} chars streamed` : ` · 已流式 ${streamChars} 字`)
          : "";
        let phase;
        if (streamChars > 0) {
          phase = currentLang() === "en"
            ? "LLM tokens arriving"
            : "模型 token 输出中";
        } else if (sec < 8) {
          phase = currentLang() === "en"
            ? "loading data + running tools"
            : "加载数据并执行工具";
        } else if (sec < 25) {
          phase = currentLang() === "en"
            ? "tools/evidence finishing, then deterministic report"
            : "工具/证据完成中，随后汇总确定性报告";
        } else {
          phase = currentLang() === "en"
            ? "waiting on deterministic tools/evidence (analysis does not depend on a model)"
            : "等待工具与证据完成（分析为确定性计算，不依赖模型）";
        }
        meta.textContent = currentLang() === "en"
          ? (`Elapsed ${sec}s · ${phase}` + streamHint)
          : (`已等待 ${sec} 秒 · ${phase}` + streamHint);
      }, 500);
    }
    function stopTimer() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    function appendStreamText(chunk) {
      if (!streamPreview) return;
      streamPreview.hidden = false;
      streamPreview.textContent += chunk;
      streamChars += (chunk || "").length;
      streamPreview.scrollTop = streamPreview.scrollHeight;
      if (copyButton) copyButton.hidden = false;
    }

    function abortActive(reason) {
      if (activeController) {
        try { activeController.abort(reason || "cancelled"); } catch (_) { /* older browsers */ }
      }
    }

    function badge(text, tone) {
      const span = document.createElement("span");
      span.className = "badge" + (tone ? (" " + tone) : "");
      span.textContent = text;
      return span;
    }

    function fillList(el, items) {
      if (!el) return;
      el.innerHTML = "";
      (items || []).forEach((item) => {
        const li = document.createElement("li");
        if (item && typeof item === "object") {
          const zh = item.zh || item.text || "";
          const en = item.en || item.text || zh;
          li.setAttribute("data-lang-zh", zh);
          li.setAttribute("data-lang-en", en);
          li.textContent = currentLang() === "en" ? en : zh;
        } else {
          li.textContent = String(item || "");
        }
        el.appendChild(li);
      });
    }

    function pickAnswer(result, view, lang) {
      if (view && view.final_answer_by_lang) {
        return view.final_answer_by_lang[lang] || view.final_answer_by_lang.zh || view.final_answer || "";
      }
      if (view && view.final_answer) return view.final_answer;
      return (result && (result.final_answer || result.answer)) || "";
    }

    function renderSoftFinal(payload, options = {}) {
      lastFinalPayload = payload;
      const lang = currentLang();
      const result = payload.result || {};
      const view = payload.view || {};
      const resultUrl = payload.result_url || "#";
      const trust = (result.trust_score && result.trust_score.score != null)
        ? result.trust_score.score
        : (view.trust_score != null ? view.trust_score : "—");
      const finalSource = result.final_answer_source || (view.answer_provenance && view.answer_provenance.final_source) || "unknown";
      const usedFinal = !!(result.used_llm_in_final);
      const llmStatus = result.llm_status || "—";
      const guard = (result.llm_guardrail && result.llm_guardrail.status) || "—";

      if (softBadges) {
        softBadges.innerHTML = "";
        softBadges.appendChild(badge(lang === "en" ? `Trust ${trust}/100` : `信任分 ${trust}/100`, ""));
        softBadges.appendChild(badge(lang === "en" ? `Source ${finalSource}` : `来源 ${finalSource}`, usedFinal ? "good" : "warn"));
        if (llmStatus === "disabled") {
          softBadges.appendChild(badge(lang === "en" ? "AI not requested (on-demand)" : "AI 未调用（按需）", ""));
        } else {
          softBadges.appendChild(badge(`LLM ${llmStatus}`, llmStatus === "success" ? "good" : ""));
        }
        softBadges.appendChild(badge(lang === "en" ? `Guardrail ${guard}` : `护栏 ${guard}`, guard === "passed" ? "good" : (guard === "failed" ? "warn" : "")));
      }
      if (softMeta) {
        softMeta.innerHTML = "";
        softMeta.appendChild(badge(lang === "en" ? `Mode ${payload.data_mode || "—"}` : `模式 ${payload.data_mode || "—"}`));
        softMeta.appendChild(badge(lang === "en" ? `result_id ${payload.result_id || "—"}` : `结果 ${payload.result_id || "—"}`));
        if (result.disclaimer) softMeta.appendChild(badge(String(result.disclaimer).slice(0, 80)));
      }

      const prov = view.answer_provenance || {};
      if (softProvHeadline) {
        softProvHeadline.textContent = lang === "en"
          ? (prov.headline_en || prov.headline || "")
          : (prov.headline_zh || prov.headline || "");
      }
      fillList(softProvLines, prov.line_items || (prov.lines || []).map((x) => ({ zh: x, en: x })));

      const lineage = view.data_lineage || {};
      if (softLineageHeadline) {
        softLineageHeadline.textContent = lineage.freshness || lineage.runtime_label || (lang === "en" ? "Data lineage" : "数据血缘");
      }
      fillList(softLineageLines, (lineage.lines || []).map((x) => ({ zh: x, en: x })));

      const softSourceLine = document.getElementById("agent-soft-source-line");
 if (softSourceLine) {
 const lineageNote = lineage.fetched_at_label || lineage.freshness || lineage.runtime_label || "";
 const sourceNote = lang === "en" ? `Source: ${finalSource}` : `来源：${finalSource}`;
 softSourceLine.textContent = lineageNote ? `${sourceNote} · ${lineageNote}` : sourceNote;
 }

      const summary = (view.answer_summary_by_lang || {})[lang] || view.answer_summary || {};
      const summarySection = document.getElementById("agent-soft-summary");
      const sentences = document.getElementById("agent-soft-sentences");
      const metrics = document.getElementById("agent-soft-metrics");
      const sentenceItems = summary.sentences || [];
      const metricItems = summary.key_metrics || [];
      if (sentences) {
        sentences.replaceChildren(...sentenceItems.map((text) => {
          const item = document.createElement("li");
          item.textContent = text;
          return item;
        }));
      }
      if (metrics) {
        metrics.replaceChildren(...metricItems.map((metric) => {
          const item = document.createElement("div");
          item.className = "metric";
          const label = document.createElement("p");
          label.className = "metric-label";
          label.textContent = (lang === "en" ? metric.label_en : metric.label_zh) || metric.label || "";
          const value = document.createElement("p");
          value.className = "metric-value";
          value.textContent = metric.value ?? "—";
          item.append(label, value);
          return item;
        }));
      }
      if (summarySection) summarySection.hidden = !sentenceItems.length && !metricItems.length;
      if (softAnswer) softAnswer.textContent = pickAnswer(result, view, lang);
      if (softOpenLink) {
        softOpenLink.href = resultUrl;
        softOpenLink.hidden = !payload.result_id;
      }
      const softAiButton = document.getElementById("agent-soft-ai-button");
      const softAiPanel = document.getElementById("agent-soft-ai-panel");
      if (softAiButton) {
        softAiButton.dataset.resultId = payload.result_id || "";
        softAiButton.dataset.dataMode = payload.data_mode || (dataModeSelect && dataModeSelect.value) || "";
        softAiButton.hidden = !payload.result_id;
      }
      if (softAiPanel) softAiPanel.hidden = !payload.result_id;
      if (softResult) {
        softResult.hidden = false;
        if (!options.languageOnly) softResult.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      if (!options.languageOnly && typeof window.applyLanguage === "function") {
        try { window.applyLanguage(lang); } catch (_) { /* optional */ }
      }
      if (!options.languageOnly && payload.result_id) {
        try {
          const url = new URL(window.location.href);
          url.pathname = "/agent";
          url.searchParams.set("result_id", payload.result_id);
          url.searchParams.set("lang", lang);
          if (payload.data_mode) url.searchParams.set("data_mode", payload.data_mode);
          window.history.replaceState({}, "", url.toString());
        } catch (_) { /* ignore */ }
      }
    }

    document.addEventListener("bondlens:language-change", () => {
      if (lastFinalPayload && softResult && !softResult.hidden) {
        renderSoftFinal(lastFinalPayload, { languageOnly: true });
      }
    });

    function currentImportId() {
      return (importIdInput && importIdInput.value) || "";
    }

    function setImportStatus(text, isError) {
      if (!importStatus) return;
      importStatus.textContent = text || "";
      importStatus.classList.toggle("form-error", !!isError);
    }

    function setImportActive(active, payload) {
      if (importFile) importFile.disabled = active;
      if (importButton) importButton.disabled = active || !(importFile && importFile.files && importFile.files.length);
      if (importClearButton) importClearButton.hidden = !active;
      if (dataModeSelect) dataModeSelect.disabled = active;
      if (importIdInput && !active) importIdInput.value = "";
      if (!active) {
        setImportStatus(importStatus && importStatus.dataset ? (currentLang() === "en" ? importStatus.dataset.langEn : importStatus.dataset.langZh) : "");
      } else if (payload) {
        const report = payload.report || {};
        const rows = report.row_count != null ? report.row_count : "—";
        const bonds = report.bond_count != null ? report.bond_count : "—";
        const missing = (report.missing_fields || []).join("、") || "";
        const notes = (report.notes_zh || []).slice(0, 2).join("；");
        const lang = currentLang();
        let text = lang === "en"
          ? `Uploaded data active: ${rows} rows / ${bonds} bonds.`
          : `已切换到上传数据：${rows} 行 / ${bonds} 只债券。`;
        if (missing) {
          text += lang === "en"
            ? ` Missing fields: ${missing} — dependent analyses are disabled.`
            : `缺少字段：${missing}——相关分析已禁用。`;
        }
        if (notes) text += " " + notes;
        setImportStatus(text);
      }
    }

    const initialImport = new URLSearchParams(window.location.search).get("import_id") || "";
    if (initialImport && importIdInput) {
      importIdInput.value = initialImport;
      setImportActive(true, null);
      setImportStatus(currentLang() === "en" ? "Uploaded dataset selected; it may need to be re-uploaded if expired." : "已选择上传数据；若已过期，请重新上传。");
    }
    if (dataModeSelect) dataModeSelect.addEventListener("change", () => window.bondlensSetSource?.(dataModeSelect.value, currentImportId()));

    if (importFile && importButton) {
      importFile.addEventListener("change", () => {
        importButton.disabled = !(importFile.files && importFile.files.length) || importFile.disabled;
      });
      importButton.addEventListener("click", async () => {
        if (!importFile.files || !importFile.files.length) return;
        const lang = currentLang();
        importButton.disabled = true;
        setImportStatus(lang === "en" ? "Uploading and parsing…" : "正在上传并解析…");
        try {
          const body = new FormData();
          body.append("file", importFile.files[0]);
          body.append("lang", lang);
          const response = await fetch("/api/import", { method: "POST", body });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(payload.error || (`HTTP ${response.status}`));
          }
          if (importIdInput) importIdInput.value = payload.import_id || "";
          window.bondlensSetSource?.(dataModeSelect?.value || "auto", currentImportId());
          setImportActive(true, payload);
        } catch (err) {
          importButton.disabled = false;
          setImportStatus((lang === "en" ? "Upload failed: " : "上传失败：") + (err && err.message ? err.message : String(err)), true);
        }
      });
    }
    if (importClearButton) {
      importClearButton.addEventListener("click", () => {
        if (importFile) importFile.value = "";
        setImportActive(false, null);
        window.bondlensSetSource?.(dataModeSelect?.value || "auto", "");
      });
    }

    if (cancelButton) {
      cancelButton.addEventListener("click", () => {
        abortActive("user_cancel");
        if (title) {
          title.textContent = currentLang() === "en" ? "Cancelling…" : "正在取消…";
        }
      });
    }
    if (copyButton) {
      copyButton.addEventListener("click", async () => {
        const textValue = streamPreview ? streamPreview.textContent : "";
        if (!textValue) return;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(textValue);
          } else {
            const ta = document.createElement("textarea");
            ta.value = textValue;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
          }
          copyButton.textContent = currentLang() === "en" ? "Copied" : "已复制";
          setTimeout(() => {
            copyButton.textContent = currentLang() === "en"
              ? (copyButton.dataset.langEn || "Copy stream preview")
              : (copyButton.dataset.langZh || "复制流式预览");
          }, 1200);
        } catch (_) {
          /* ignore */
        }
      });
    }
    if (softCopyAnswer) {
      softCopyAnswer.addEventListener("click", async () => {
        const textValue = softAnswer ? softAnswer.textContent : "";
        if (!textValue) return;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(textValue);
          }
          softCopyAnswer.textContent = currentLang() === "en" ? "Copied" : "已复制";
          setTimeout(() => {
            softCopyAnswer.textContent = currentLang() === "en"
              ? (softCopyAnswer.dataset.langEn || "Copy final answer")
              : (softCopyAnswer.dataset.langZh || "复制最终回答");
          }, 1200);
        } catch (_) { /* ignore */ }
      });
    }

    async function runJsonFallback(endpoint, lang, question, dataMode, signal) {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
        },
        body: JSON.stringify({
          question,
          data_mode: dataMode,
          lang,
          include_view: true,
          import_id: currentImportId(),
        }),
        signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || (`HTTP ${response.status}`));
      }
      if (!payload.result_id && !payload.final_answer) {
        throw new Error(lang === "en" ? "Missing result from API." : "接口未返回结果。");
      }
      // Normalize shape to soft renderer
      const body = {
        result_id: payload.result_id,
        result_url: payload.result_url,
        result: payload.result || payload,
        view: payload.view || null,
        data_mode: dataMode,
        lang,
      };
      if (!body.view) {
        // Minimal view for soft card when include_view failed
        body.view = {
          final_answer: body.result.final_answer || body.result.answer || "",
          answer_provenance: {
            headline: lang === "en" ? "Answer provenance unavailable in soft view." : "软渲染未附带完整 provenance。",
            lines: [],
            line_items: [],
          },
          data_lineage: { lines: [], freshness: "" },
        };
      }
      renderSoftFinal(body);
    }

    async function runSse(streamEndpoint, lang, question, dataMode, signal) {
      const response = await fetch(streamEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
        },
        body: JSON.stringify({
          question,
          data_mode: dataMode,
          lang,
          import_id: currentImportId(),
        }),
        signal,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || (`HTTP ${response.status}`));
      }
      if (!response.body || !response.body.getReader) {
        throw new Error("SSE body unavailable");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let currentEvent = "message";
      let finalPayload = null;

      function handleBlock(block) {
        const lines = block.split(/\r?\n/);
        let eventName = currentEvent;
        const dataLines = [];
        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
          }
        }
        if (!dataLines.length) return;
        let payload = {};
        try {
          payload = JSON.parse(dataLines.join("\n"));
        } catch {
          return;
        }
        if (eventName === "status") {
          const msg = payload.message || (lang === "en" ? payload.message_en : payload.message_zh) || "";
          if (title && msg) title.textContent = msg;
          appendToolStep(msg, payload.stage, payload.tool);
        } else if (eventName === "token") {
          appendStreamText(payload.text || "");
          if (title) {
            title.textContent = lang === "en" ? "Streaming LLM tokens…" : "正在流式输出 LLM…";
          }
        } else if (eventName === "final") {
          finalPayload = payload;
        } else if (eventName === "error") {
          throw new Error(payload.error || "stream error");
        }
      }

      while (true) {
        if (signal && signal.aborted) {
          try { await reader.cancel(); } catch (_) {}
          throw new DOMException("Aborted", "AbortError");
        }
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let splitAt;
        while ((splitAt = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, splitAt);
          buffer = buffer.slice(splitAt + 2);
          if (block.trim()) handleBlock(block);
        }
      }
      if (buffer.trim()) handleBlock(buffer);
      if (!finalPayload) {
        throw new Error(lang === "en" ? "Stream ended without final result." : "流式结束但未收到最终结果。");
      }
      markStepsDone();
      renderSoftFinal(finalPayload);
    }

    form.addEventListener("submit", async (event) => {
      if (!window.fetch) {
        setBusy(true);
        return;
      }
      event.preventDefault();
      if (button.disabled) return;

      const streamEndpoint = form.dataset.streamEndpoint || "/api/agent/stream";
      const jsonEndpoint = form.dataset.asyncEndpoint || "/api/agent/query";
      const lang = currentLang();
      const question = (form.question && form.question.value || "").trim();
      const dataMode = (form.data_mode && form.data_mode.value) || "auto";
      if (!question) {
        if (errorBox) {
          errorBox.hidden = false;
          errorBox.textContent = lang === "en" ? "Please enter a question." : "请先输入问题。";
        }
        return;
      }

      abortActive("superseded");
      activeController = (typeof AbortController !== "undefined") ? new AbortController() : null;
      const signal = activeController ? activeController.signal : undefined;

      setBusy(true);
      startTimer();
      try {
        // Choose before sending: retrying a failed SSE POST can duplicate a paid analysis.
        if (window.ReadableStream && window.TextDecoder) {
          await runSse(streamEndpoint, lang, question, dataMode, signal);
        } else {
          await runJsonFallback(jsonEndpoint, lang, question, dataMode, signal);
        }
        stopTimer();
        setBusy(false);
        activeController = null;
        if (title) {
          title.textContent = lang === "en" ? "Done" : "完成";
        }
        if (progress) progress.hidden = false;
      } catch (err) {
        stopTimer();
        setBusy(false);
        activeController = null;
        if (progress) progress.hidden = false;
        if (errorBox) {
          errorBox.hidden = false;
          const aborted = (err && (err.name === "AbortError" || String(err).includes("abort")));
          if (title) title.textContent = aborted
            ? (lang === "en" ? "Stopped" : "已停止")
            : (lang === "en" ? "Analysis failed" : "分析失败");
          if (aborted) {
            errorBox.textContent = lang === "en"
              ? "Stopped receiving. Analysis may still finish on the server."
              : "已停止接收。服务端分析可能仍在完成。";
          } else {
            errorBox.textContent = (lang === "en" ? "Request failed: " : "请求失败：") + (err && err.message ? err.message : String(err));
          }
        }
      }
    });
  })();

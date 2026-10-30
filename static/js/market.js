/* 市场观察 page renderer. */
(() => {
  const root = document.querySelector("[data-market-root]");
  if (!root) return;
  const WB = window.BondLensWorkbench;

  function card(label, value, suffix) {
    return `<div class="wb-card card"><p class="wb-card-label">${label}</p>
        <p class="wb-card-value">${value}${suffix ? ` <small>${suffix}</small>` : ""}</p></div>`;
  }

  function identityItem(key, value) {
    return `<div class="wb-identity-item"><span class="k">${WB.escapeHtml(key)}</span><span class="v">${WB.escapeHtml(value)}</span></div>`;
  }

  function render(overview) {
    root.innerHTML = "";
    const source = overview.data_source || {};
    const quality = overview.data_quality || {};
    const yieldSummary = overview.yield_summary || {};
    const volumeSummary = overview.volume_summary || {};
    const maturitySummary = overview.maturity_summary_years || {};
    const weighted = overview.weighted_yield_summary || {};
    const zh = WB.lang() === "zh";

    /* Identity + honesty strip: one compact summary line up front; the full
       caliber grid folds into an expandable “数据与口径” block so research
       data stays above the fold without hiding the caveats. */
    const identity = document.createElement("section");
    identity.className = "wb-panel card";
    const quoteWhen = WB.quoteWhen(source);
    const runtime = WB.runtimeInfo(source);
    const summaryBits = [
      `${zh ? "来源" : "Source"} ${source.source_name || "—"}${source.provider ? ` (${source.provider})` : ""}`,
      `${zh ? "最近行情" : "Latest quote"} ${WB.quoteWhen(source, true)}`,
      ...(source.fetched_at ? [`${zh ? "获取时间" : "Fetched"} ${WB.fmtFetchedAt(source.fetched_at)}`] : []),
      `${zh ? "样本" : "Rows"} ${WB.fmt(overview.sample_count, 0)}${zh ? " 行" : ""} / ${WB.fmt(source.bond_count, 0)}${zh ? " 券" : " bonds"}`,
    ];
    identity.innerHTML = `<div class="wb-panel-header source-strip-header">
            <h2 class="wb-panel-title">${zh ? "数据来源与口径" : "Source & caliber"}</h2>
            <div class="wb-badge-row"><span class="wb-badge ${runtime.isLive ? "good" : "warn"}">${runtime.label}</span></div>
        </div>
        <div class="wb-panel-body source-strip-body">
          <p class="source-summary"></p>
        </div>`;
    identity.querySelector(".source-summary").textContent = summaryBits.join(" · ");
    const identityBody = identity.querySelector(".source-strip-body");
    const warnText = source.mixed_timepoints
      ? (zh
        ? "样本包含多个日期：下表统计仅描述样本构成，不同时点的收益率不可作为同一截面对比。"
        : "The sample spans multiple dates: the tables below describe sample composition only; yields across timepoints are not one cross-section.")
      : (source.fallback_reason ? `${zh ? "回退原因" : "Fallback reason"}: ${source.fallback_reason}` : "");
    if (warnText) {
      const warn = document.createElement("p");
      warn.className = "report-warn";
      warn.textContent = warnText;
      identityBody.append(warn);
    }
    const details = document.createElement("details");
    details.className = "source-details";
    const grid = document.createElement("div");
    grid.className = "wb-identity-grid";
    grid.innerHTML = [
      identityItem(zh ? "行情时点" : "Quote time", quoteWhen),
      identityItem(zh ? "获取时间（北京时间）" : "Fetched at (UTC+8)", WB.fmtFetchedAt(source.fetched_at)),
      identityItem(zh ? "债券代码列" : "Code column", source.has_code_column ? (zh ? "可用" : "available") : (zh ? "不可用（按简称检索）" : "unavailable (search by name)")),
      identityItem(zh ? "记录粒度" : "Record granularity", zh ? (source.record_granularity_zh || "—") : (source.record_granularity_en || "—")),
    ].join("");
    const summary = document.createElement("summary");
    summary.textContent = zh ? "数据与口径细节" : "Data & caliber details";
    details.append(summary, grid);
    identityBody.append(details);
    root.append(identity);

    /* Metric cards */
    const cards = document.createElement("div");
    cards.className = "wb-grid market-kpis";
    cards.innerHTML =
      card(zh ? "样本记录 / 债券" : "Rows / bonds", `${WB.fmt(overview.sample_count, 0)} / ${WB.fmt(source.bond_count, 0)}`) +
      card(zh ? "有效收益率" : "Valid yields", WB.fmt(yieldSummary.count, 0)) +
      card(zh ? "收益率中位" : "Yield median", `${WB.fmt(yieldSummary.median, 2)}<small>%</small>`) +
      card(zh ? "待偿期中位" : "Maturity median", `${WB.fmt(maturitySummary.median, 2)}<small>${zh ? "年" : "yrs"}</small>`) +
      card(zh ? "成交中位" : "Volume median", volumeSummary.count ? `${WB.fmt(volumeSummary.median, 2)}<small>${zh ? "亿元" : "100M"}</small>` : "—");
    root.append(cards);
    if (weighted.weighted_mean !== undefined) {
      const weightedNote = document.createElement("p");
      weightedNote.className = "market-weighted-note";
      weightedNote.textContent = `${zh ? "交易量加权收益率" : "Volume-weighted yield"}：${WB.fmt(weighted.weighted_mean, 2)}%`;
      root.append(weightedNote);
    }

    /* Yield distribution bars */
    const dist = overview.yield_distribution || {};
    const entries = Object.entries(dist);
    const maxCount = entries.length ? Math.max(...entries.map(([, count]) => Number(count))) : 1;
    const distPanel = document.createElement("section");
    distPanel.className = "wb-panel card";
    distPanel.innerHTML = `<div class="wb-panel-header">
            <h2 class="wb-panel-title">${zh ? "收益率分布" : "Yield distribution"}</h2>
            <p class="wb-panel-subtitle">${zh ? "按固定收益率区间统计记录数；≥6% 作为尾部单独列出，不压缩主图刻度。" : "Row counts by fixed yield intervals; the ≥6% tail is listed as its own column so extremes never stretch the scale."}</p>
        </div>`;
    const distBody = document.createElement("div");
    distBody.className = "wb-panel-body";
    if (entries.length) {
      const bars = document.createElement("div");
      bars.className = "wb-bars";
      bars.innerHTML = entries.map(([label, count]) => {
        const width = maxCount ? Math.max(2, Math.round((Number(count) / maxCount) * 100)) : 2;
        const isTail = String(label).indexOf("≥") === 0;
        const display = isTail ? (zh ? `${label}（尾部）` : `${label} (tail)`) : String(label);
        const fillStyle = `width:${width}%${isTail ? ";background:#b3543a" : ""}`;
        return `<div class="wb-bar-row"><span>${display}</span>
                <span class="wb-bar-track"><span class="wb-bar-fill" style="${fillStyle}"></span></span>
                <span>${WB.fmt(count, 0)}</span></div>`;
      }).join("");
      distBody.append(bars);
    } else {
      distBody.innerHTML = `<p class="wb-empty">${zh ? "当前样本没有可用的收益率数据。" : "No usable yield data in this sample."}</p>`;
    }
    distPanel.append(distBody);

    /* Segments tables */
    const segments = overview.segments || {};
    const segPanel = document.createElement("section");
    segPanel.className = "wb-panel card";
    segPanel.innerHTML = `<div class="wb-panel-header">
            <h2 class="wb-panel-title">${zh ? "券种与期限结构" : "Type & maturity structure"}</h2>
            <p class="wb-panel-subtitle">${zh
              ? "同一快照内的分组统计：记录数与收益率中位数。"
              : "Within-snapshot group statistics: row counts and median yields."}</p>
        </div>`;
    const segBody = document.createElement("div");
    segBody.className = "wb-panel-body nopad";
    const typeRows = segments.by_bond_type || [];
    const bucketRows = segments.by_maturity_bucket || [];
    segBody.innerHTML =
      `<div class="wb-table-wrap"><table class="wb-table"><thead><tr>
            <th>${zh ? "券种" : "Type"}</th><th>${zh ? "记录数" : "Rows"}</th><th>${zh ? "收益率中位（%）" : "Yield median (%)"}</th></tr></thead><tbody>${
        typeRows.map((row) => `<tr><td>${row.bond_type}</td><td class="num">${WB.fmt(row.count, 0)}</td><td class="num">${WB.fmt(row.yield_median, 2)}</td></tr>`).join("")
      }</tbody></table></div>
        <div class="wb-table-wrap"><table class="wb-table"><thead><tr>
            <th>${zh ? "期限分桶" : "Maturity bucket"}</th><th>${zh ? "记录数" : "Rows"}</th><th>${zh ? "收益率中位（%）" : "Yield median (%)"}</th></tr></thead><tbody>${
        bucketRows.map((row) => `<tr><td>${row.bucket}</td><td class="num">${WB.fmt(row.count, 0)}</td><td class="num">${WB.fmt(row.yield_median, 2)}</td></tr>`).join("")
      }</tbody></table></div>`;
    segPanel.append(segBody);
    const analyses = document.createElement("div");
    analyses.className = "bl-analysis-grid";
    analyses.append(distPanel, segPanel);
    root.append(analyses);

    /* Data quality */
    const qualityPanel = document.createElement("section");
    qualityPanel.className = "wb-panel card";
    const levelBadge = quality.level === "high"
      ? "good"
      : (quality.level === "medium" ? "warn" : "bad");
    qualityPanel.innerHTML = `<div class="wb-panel-header">
            <h2 class="wb-panel-title">${zh ? "样本质量" : "Sample quality"}</h2>
            <div class="wb-badge-row"><span class="wb-badge ${levelBadge}">${quality.level || "—"} · ${WB.fmt(quality.score, 0)}</span></div>
        </div>`;
    const qualityBody = document.createElement("div");
    qualityBody.className = "wb-panel-body";
    qualityBody.innerHTML = `<p style="margin:0 0 8px; font-size:13px;">${zh ? quality.summary_zh : quality.summary_en}</p>
        <ul class="wb-record-list">${(quality.issues || []).map((issue) => `<li>${zh ? issue.message_zh : issue.message_en}</li>`).join("")}</ul>`;
    qualityPanel.append(qualityBody);
    root.append(qualityPanel);

    /* Quick ask (secondary entry to the agent) */
    const ask = document.createElement("section");
    ask.className = "wb-panel card";
    ask.innerHTML = `<div class="wb-panel-header"><div>
            <h2 class="wb-panel-title">${zh ? "快速提问" : "Quick question"}</h2>
            <p class="wb-panel-subtitle">${zh ? "跳转到智能体，对当前样本继续提问。" : "Continue with a question to the agent."}</p>
        </div></div>`;
    const askBody = document.createElement("div");
    askBody.className = "wb-panel-body";
    askBody.innerHTML = `<div class="wb-quick-ask">
            <input type="text" data-quick-question data-placeholder-zh="例如：3 年内到期的国债，收益率如何？"
                   data-placeholder-en="e.g. How are yields on CGBs maturing within 3 years?"
                   placeholder="${zh ? "例如：3 年内到期的国债，收益率如何？" : "e.g. How are yields on CGBs maturing within 3 years?"}">
            <button type="button" class="wb-button" data-quick-ask>${zh ? "在智能体中提问" : "Ask the agent"}</button>
        </div>`;
    askBody.querySelector("[data-quick-ask]").addEventListener("click", () => {
      const input = askBody.querySelector("[data-quick-question]");
      const question = (input.value || "").trim();
      if (!question) { input.focus(); return; }
      const query = WB.params();
      query.set("question", question);
      window.location.href = `/agent?${query.toString()}`;
    });
    askBody.querySelector("[data-quick-question]").addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        askBody.querySelector("[data-quick-ask]").click();
      }
    });
    ask.append(askBody);
    root.append(ask);
  }

  let loadSeq = 0;
  async function load() {
    const seq = ++loadSeq;
    try {
      const overview = await WB.fetchJson(`/api/market/overview?${WB.params().toString()}`);
      if (seq !== loadSeq) return; // a newer request superseded this one
      WB.setActiveSource(overview.data_source);
      render(overview);
    } catch (error) {
      if (seq !== loadSeq) return;
      WB.setSourceError(error.message || error);
      root.innerHTML = `<div class="wb-error-box">${WB.escapeHtml(error.message || error)}</div>`;
    }
  }

  WB.init({ render: load });
})();

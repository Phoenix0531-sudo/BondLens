/* 单券研究 page renderer: key lookup → /api/bond/detail; honest comparison, no fabricated trends. */
(() => {
  const searchForm = document.querySelector("[data-bond-search]");
  const keyInput = document.querySelector("[data-bond-key-input]");
  const root = document.querySelector("[data-bond-root]");
  if (!searchForm || !keyInput || !root) return;
  const WB = window.BondLensWorkbench;

  let activeKey = (root.dataset.bondKey || "").trim();

  function card(label, value, suffix) {
    return `<div class="wb-card card"><p class="wb-card-label">${label}</p>
        <p class="wb-card-value">${value}${suffix ? ` <small>${suffix}</small>` : ""}</p></div>`;
  }

  function emptyState() {
    const zh = WB.lang() === "zh";
    root.innerHTML = `<section class="wb-panel card"><div class="wb-panel-body">
        <p class="wb-empty">${zh
          ? "在上方输入债券代码或简称开始研究。<br>将从当前数据源中定位该券的行情，并与同一时点、同券种、同期限桶的同业对比。"
          : "Enter a bond code or name above.<br>BondLens locates its quotes in the active source and compares with same-timepoint, same-type, same-bucket peers."}</p>
    </div></section>`;
  }

  function askLink(name) {
    const zh = WB.lang() === "zh";
    const query = WB.params();
    query.set("question", zh ? `结合同业对比，分析 ${name} 的收益率水平与成交情况` : `Analyze the yield level and trading of ${name} versus its peers`);
    return `/agent?${query.toString()}`;
  }

  function render(payload) {
    const zh = WB.lang() === "zh";
    const target = payload.target || {};
    const comparison = payload.comparison || {};
    const peer = comparison.peer_comparison || {};
    const rate = comparison.rate_sensitivity || {};
    const credit = comparison.credit_context || {};
    root.innerHTML = "";

    /* Identity */
    const sourceMeta = payload.data_source || {};
    const quoteWhen = WB.quoteWhen(sourceMeta);
    const bondQuoteWhen = target["行情时间"] || null;
    const runtime = WB.runtimeInfo(sourceMeta);
    const identity = document.createElement("section");
    identity.className = "wb-panel card";
    identity.innerHTML = `<div class="wb-panel-header"><div class="wb-bond-identity">
            <h2>${target.name || "—"}</h2>
            ${target["债券代码"] ? `<span class="code">${target["债券代码"]}</span>` : ""}
            <span class="wb-badge">${target.type_value || "—"}</span>
            ${target["期限分桶"] ? `<span class="wb-badge">${target["期限分桶"]}</span>` : ""}
            <span class="wb-badge ${runtime.isLive ? "good" : "warn"}">${runtime.label}</span>
            <p class="wb-panel-subtitle" data-bond-source-info style="margin-top:6px"></p>
        </div>
        <a class="ghost-button" href="${askLink(target.name || "")}">${zh ? "在智能体中继续研究" : "Continue in the agent"}</a>
    </div>`;
    identity.querySelector("[data-bond-source-info]").textContent = [
      `${zh ? "来源" : "Source"} ${sourceMeta.source_name || "—"}`,
      `${bondQuoteWhen ? (zh ? "该券行情" : "Bond quote") : (zh ? "行情范围" : "Quote range")} ${bondQuoteWhen || quoteWhen}`,
      ...(sourceMeta.fetched_at ? [`${zh ? "获取" : "Fetched"} ${WB.fmtFetchedAt(sourceMeta.fetched_at)}`] : []),
    ].join(" · ");
    root.append(identity);

    /* Quote cards */
    const cards = document.createElement("div");
    cards.className = "wb-grid wb-grid-four";
    cards.innerHTML =
      card(zh ? "收益率" : "Yield", `${WB.fmt(target.yield_value, 4)}<small>%</small>`) +
      card(zh ? "收盘净价" : "Net price", WB.fmt(target["收盘净价(元)"], 2), zh ? "元" : "CNY") +
      card(zh ? "成交量" : "Volume", WB.fmt(target.volume_value, 2), zh ? "亿元" : "100M") +
      card(zh ? "待偿期" : "Maturity", target.maturity_value ?? "—");
    root.append(cards);

    /* Market comparison */
    const compPanel = document.createElement("section");
    compPanel.className = "wb-panel card";
    const outlierBadge = comparison.is_yield_outlier
      ? `<span class="wb-badge bad">${zh ? "同桶收益率异常" : "Yield outlier"}</span>`
      : "";
    compPanel.innerHTML = `<div class="wb-panel-header"><div>
            <h2 class="wb-panel-title">${zh ? "与市场及同业的对比" : "Versus market & peers"}</h2>
            <p class="wb-panel-subtitle">${zh
              ? "同一数据时点内计算；不跨时点、不跨券种拼凑可比组。"
              : "Computed within one snapshot; peers never widened across timepoints or types."}</p>
        </div><div class="wb-badge-row">${outlierBadge}</div></div>`;
    const compBody = document.createElement("div");
    compBody.className = "wb-panel-body";

    const compTable = document.createElement("div");
    compTable.className = "wb-table-wrap";
    const row = (label, value, hint) => `<tr><td>${label}${hint ? ` <span style="color:var(--muted); font-size:13px;">(${hint})</span>` : ""}</td><td class="num">${value}</td></tr>`;
    compTable.innerHTML = `<table class="wb-table"><tbody>
        ${row(zh ? "收益率分位（全样本）" : "Yield percentile (all)", `${WB.fmt(comparison.yield_percentile, 1)}%`)}
        ${row(zh ? "收益率 z 分数" : "Yield z-score", WB.fmt(comparison.yield_zscore, 2))}
        ${row(zh ? "期限分位" : "Maturity percentile", `${WB.fmt(comparison.maturity_percentile, 1)}%`)}
        ${row(zh ? "成交量分位" : "Volume percentile", `${WB.fmt(comparison.volume_percentile, 1)}%`)}
    </tbody></table>`;
    compBody.append(compTable);

    if (peer.peer_count) {
      const peerTable = document.createElement("div");
      peerTable.className = "wb-table-wrap";
      peerTable.style.marginTop = "10px";
      peerTable.innerHTML = `<table class="wb-table"><thead><tr>
            <th>${zh ? "同业对比" : "Peer comparison"}</th><th>${zh ? "值" : "Value"}</th></tr></thead><tbody>
        ${row(zh ? "同业样本数" : "Peer count", WB.fmt(peer.peer_count, 0))}
        ${row(zh ? "同业收益率均值" : "Peer yield mean", `${WB.fmt(peer.peer_yield_mean, 4)}%`)}
        ${row(zh ? "同业收益率中位" : "Peer yield median", `${WB.fmt(peer.peer_yield_median, 4)}%`)}
        ${row(zh ? "相对同业均值利差" : "Spread vs peer mean", `${WB.fmt(peer.spread_vs_peer_mean_bp, 1)} bp`)}
        ${row(zh ? "同业内收益率分位" : "Yield pct within peers", `${WB.fmt(peer.peer_yield_percentile, 1)}%`)}
      </tbody></table>
      <p class="wb-panel-subtitle" style="margin:6px 0 0;">${zh ? peer.peer_definition_zh : peer.peer_definition_en}</p>`;
      compBody.append(peerTable);
    } else {
      const note = document.createElement("p");
      note.className = "report-warn";
      note.textContent = zh
        ? "当前样本缺少必要的对比字段（券种/期限/收益率），无法构造同业可比组。"
        : "The sample lacks fields needed for a peer group (type/maturity/yield); peer comparison is unavailable.";
      compBody.append(note);
    }
    if (comparison.nearest_market_context) {
      const context = document.createElement("p");
      context.className = "wb-panel-subtitle";
      context.style.marginTop = "10px";
      context.textContent = comparison.nearest_market_context;
      compBody.append(context);
    }
    compPanel.append(compBody);
    root.append(compPanel);

    /* On-demand AI interpretation (explicit click only; reuses the deterministic comparison above) */
    const aiPanel = document.createElement("section");
    aiPanel.className = "wb-panel card";
    aiPanel.setAttribute("data-ai-explain", "");
    aiPanel.innerHTML = `<div class="wb-panel-header"><div>
            <h2 class="wb-panel-title">${zh ? "AI 解读（可选，需显式点击）" : "AI interpretation (optional, click to run)"}</h2>
            <p class="wb-panel-subtitle">${zh
              ? "仅在你点击后调用一次外部模型：复用上方已计算的确定性对比，不重算数据、不给投资建议；未配置或失败时本页结果不受影响。上传数据需先确认才会发送摘要。"
              : "Only your click triggers one external model call: it reuses the deterministic comparison above — no recomputation, no investment advice; if unconfigured or failed, this page is unaffected. Uploaded data requires confirmation before any summary is sent."}</p>
        </div></div>`;
    const aiBodyWrap = document.createElement("div");
    aiBodyWrap.className = "wb-panel-body";
    const aiButton = document.createElement("button");
    aiButton.type = "button";
    aiButton.className = "ghost-button";
    aiButton.setAttribute("data-ai-button", "");
    aiButton.setAttribute("data-lang-zh", "生成 AI 解读");
    aiButton.setAttribute("data-lang-en", "Generate AI interpretation");
    aiButton.textContent = zh ? "生成 AI 解读" : "Generate AI interpretation";
    aiButton.dataset.code = target["债券代码"] || "";
    aiButton.dataset.name = target.name || "";
    const pageParams = WB.params();
    aiButton.dataset.dataMode = pageParams.get("data_mode") || "";
    aiButton.dataset.importId = pageParams.get("import_id") || "";
    const aiStatus = document.createElement("p");
    aiStatus.className = "wb-panel-subtitle";
    aiStatus.setAttribute("data-ai-status", "");
    aiStatus.hidden = true;
    const aiText = document.createElement("div");
    aiText.setAttribute("data-ai-body", "");
    aiText.hidden = true;
    aiText.style.cssText = "border:1px solid var(--border,#d9dce3);border-radius:8px;padding:12px;white-space:pre-wrap;font-size:14px;line-height:1.7;";
    aiBodyWrap.append(aiButton, aiStatus, aiText);
    aiPanel.append(aiBodyWrap);
    root.append(aiPanel);

    /* Secondary technical material stays available without dominating the page. */
    const technical = document.createElement("details");
    technical.className = "wb-panel card bond-technical";
    const technicalSummary = document.createElement("summary");
    technicalSummary.textContent = zh ? "技术参数与研究依据" : "Technical parameters and research basis";
    technical.append(technicalSummary);
    root.append(technical);

    /* Rate sensitivity */
    if (rate && (rate.modified_duration != null || rate.dv01 != null)) {
      const ratePanel = document.createElement("section");
      ratePanel.className = "wb-panel card";
      ratePanel.innerHTML = `<div class="wb-panel-header"><div>
            <h2 class="wb-panel-title">${zh ? "利率敏感性（教学口径）" : "Rate sensitivity (teaching caliber)"}</h2>
            <p class="wb-panel-subtitle">${zh ? (rate.note_zh || "") : (rate.note_en || "")}</p>
        </div></div>`;
      const rateBody = document.createElement("div");
      rateBody.className = "wb-panel-body";
      rateBody.innerHTML = `<div class="wb-table-wrap"><table class="wb-table"><tbody>
            ${row(zh ? "修正久期" : "Modified duration", WB.fmt(rate.modified_duration, 4))}
            ${row(zh ? "麦考利久期" : "Macaulay duration", WB.fmt(rate.macaulay_duration, 4))}
            ${row("DV01", `${WB.fmt(rate.dv01, 4)} ${zh ? "元/亿元面额" : "per 100M face"}`)}
        </tbody></table>
        <p class="wb-panel-subtitle" style="margin:8px 0 0;">${zh ? (rate.assumptions_zh || "") : (rate.assumptions_en || "")}</p></div>`;
      ratePanel.append(rateBody);
      technical.append(ratePanel);
    }

    /* Credit boundary (honesty) */
    if (credit && credit.available === false) {
      const creditPanel = document.createElement("section");
      creditPanel.className = "wb-panel card";
      creditPanel.innerHTML = `<div class="wb-panel-header"><h2 class="wb-panel-title">${zh ? "信用边界" : "Credit boundary"}</h2></div>
        <div class="wb-panel-body"><p class="wb-panel-subtitle" style="margin:0;">${zh ? (credit.note_zh || "") : (credit.note_en || "")}</p></div>`;
      technical.append(creditPanel);
    }

    /* Records (multi-row honesty) */
    const recordsPanel = document.createElement("section");
    recordsPanel.className = "wb-panel card";
    recordsPanel.innerHTML = `<div class="wb-panel-header"><h2 class="wb-panel-title">${zh ? "样本内记录" : "Records in sample"}</h2></div>`;
    const recordsBody = document.createElement("div");
    recordsBody.className = "wb-panel-body nopad";
    const records = payload.records || [];
    recordsBody.innerHTML = `<div class="wb-table-wrap"><table class="wb-table"><thead><tr>
            <th>${zh ? "债券简称" : "Name"}</th><th>${zh ? "收益率(%)" : "Yield(%)"}</th>
            <th>${zh ? "成交量(亿元)" : "Volume"}</th><th>${zh ? "收盘净价(元)" : "Net price"}</th></tr></thead><tbody>${
      records.map((record) => `<tr>
            <td>${record.name || "—"}</td><td class="num">${WB.fmt(record.yield_value, 4)}</td>
            <td class="num">${WB.fmt(record.volume_value, 2)}</td><td class="num">${WB.fmt(record["收盘净价(元)"], 2)}</td></tr>`).join("")
    }</tbody></table></div>`;
    recordsPanel.append(recordsBody);
    technical.append(recordsPanel);

    const notes = [...(payload.notes || [])];
    if (payload.multi_row) {
      const extra = zh ? (payload.data_source || {}).record_granularity_zh : (payload.data_source || {}).record_granularity_en;
      if (extra) notes.push(extra);
    }
    if (notes.length) {
      const noteList = document.createElement("ul");
      noteList.className = "wb-notes";
      notes.forEach((note) => {
        const item = document.createElement("li");
        item.textContent = note;
        noteList.append(item);
      });
      technical.append(noteList);
    }
  }

  let loadSeq = 0;
  async function loadSourceProfile(seq) {
    try {
      const profile = await WB.fetchJson(`/api/data/source?${WB.params().toString()}`);
      if (seq === loadSeq) WB.setActiveSource(profile);
    } catch (error) {
      if (seq === loadSeq) WB.setSourceError(error.message || error);
    }
  }

  async function load(key) {
    const seq = ++loadSeq;
    if (!key) {
      emptyState();
      await loadSourceProfile(seq);
      return;
    }
    const zh = WB.lang() === "zh";
    root.innerHTML = `<p class="wb-loading">${zh ? "正在加载…" : "Loading…"}</p>`;
    const query = WB.params();
    query.set("key", key);
    try {
      const payload = await WB.fetchJson(`/api/bond/detail?${query.toString()}`);
      if (seq !== loadSeq) return; // a newer request superseded this one
      WB.setActiveSource(payload.data_source);
      render(payload);
    } catch (error) {
      if (seq !== loadSeq) return;
      if (error.status === 404) loadSourceProfile(seq);
      else WB.setSourceError(error.message || error);
      const hint = error.status === 404
        ? (zh ? "当前数据样本中没有这只债券。请检查代码/简称，或切换数据源。" : "This bond is not in the active sample. Check the code/name or switch the data source.")
        : "";
      root.innerHTML = `<div class="wb-error-box">${WB.escapeHtml(error.message || error)}${hint ? `<br>${WB.escapeHtml(hint)}` : ""}</div>`;
    }
  }

  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    activeKey = (keyInput.value || "").trim();
    if (activeKey) load(activeKey);
    else load("");
  });

  WB.init({ render: () => load(activeKey) });
})();

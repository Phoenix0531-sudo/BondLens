/* Shared workbench runtime: data-source bar, import panel, mapping report,
 * correction flow, query params, language events. Used by market/screen/bond pages. */
(() => {
  const state = {
    mode: "auto",
    importId: "",
    file: null,
    report: null,
    source: null,
    activeSource: null,
    sourceError: null,
    render: null,
  };

  const lang = () => (document.documentElement.lang || "zh").startsWith("en") ? "en" : "zh";
  const t = (zh, en) => (lang() === "en" ? en : zh);

  const fmt = (value, digits = 2) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  };

  const fmtDate = (value) => (value ? String(value).slice(0, 10) : "—");

  const runtimeInfo = (source = {}) => {
    const labels = {
      live: ["在线行情", "Live feed", "live-tag", true],
      live_feed: ["在线行情", "Live feed", "live-tag", true],
      live_snapshot: ["缓存行情", "Cached quotes", "fallback-tag", false],
      static_fallback: ["样例回退", "Sample fallback", "fallback-tag", false],
      static_sample: ["历史样例", "Historical sample", "sample-tag", false],
      import: ["上传样本", "Imported sample", "import-tag", false],
    };
    const entry = labels[source.runtime_mode] || ["来源未确认", "Unverified source", "sample-tag", false];
    return { label: t(entry[0], entry[1]), tag: entry[2], isLive: entry[3] };
  };

  const quoteWhen = (source = {}, compact = false) => {
    if (source.mixed_timepoints) {
      return source.quote_date_range || t("多个行情日期", "Multiple quote dates");
    }
    if (source.quote_time) {
      const range = String(source.quote_time);
      return compact && range.includes("~") ? range.split("~").pop().trim() : range;
    }
    if (source.sample_date) return fmtDate(source.sample_date);
    if (source.quote_date_range) return String(source.quote_date_range);
    return t("行情时间未提供", "Quote time unavailable");
  };

  const fmtFetchedAt = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(date);
    const get = (type) => parts.find((part) => part.type === type)?.value || "";
    return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} UTC+8`;
  };

  const sourceShortName = (source = {}) => {
    if (source.source_id === "chinamoney_bond_spot_deal") return t("中国货币网", "ChinaMoney");
    if (source.source_id === "chinamoney_bond_spot_deal_snapshot") return t("中国货币网缓存", "ChinaMoney cache");
    if (source.source_id === "local_static_excel") return t("本地示例文件", "Local sample file");
    return source.source_name || t("当前数据源", "Active source");
  };

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

  /* Query params carried across API calls and page navigations. */
  function params() {
    const query = new URLSearchParams();
    if (state.mode) query.set("data_mode", state.mode);
    if (state.importId) query.set("import_id", state.importId);
    const activeLang = lang();
    query.set("lang", activeLang);
    return query;
  }

  const paramObject = () => Object.fromEntries(params().entries());

  async function fetchJson(url, options) {
    // Bound read requests only; uploads are not retried or timed out here.
    const isRead = !options || !options.method || options.method.toUpperCase() === "GET";
    const controller = isRead && !(options && options.signal) ? new AbortController() : null;
    let timedOut = false;
    const timer = controller ? setTimeout(() => { timedOut = true; controller.abort(); }, 30000) : null;
    let response;
    let body = null;
    try {
      response = await fetch(url, controller ? { ...options, signal: controller.signal } : options);
      try { body = await response.json(); } catch (error) {
        if (timedOut) throw error;
        /* non-JSON error body */
      }
    } catch (error) {
      if (timedOut) throw new Error(t("数据请求超时，请重试或切换数据源。", "Data request timed out. Retry or switch data source."));
      throw error;
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (!response.ok) {
      const detail = body && (body.error || body.message || JSON.stringify(body));
      const error = new Error(typeof detail === "string" ? detail : t("请求失败", "Request failed"));
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  }

  /* ---------- Import panel ---------- */
  const el = {};
  const collect = () => {
    el.bar = document.querySelector("[data-source-bar]");
    if (!el.bar) return false;
    el.mode = el.bar.querySelector("[data-mode-select]");
    el.file = el.bar.querySelector("[data-import-file]");
    el.upload = el.bar.querySelector("[data-import-upload]");
    el.clear = el.bar.querySelector("[data-import-clear]");
    el.status = el.bar.querySelector("[data-source-status]");
    el.report = el.bar.querySelector("[data-import-report]");
    return true;
  };

  const MISSING_FIELD_LABELS = {
    "债券简称": { zh: "债券简称（必需）", en: "Bond name (required)" },
    "收盘到期收益率(%)": { zh: "收盘到期收益率(%)", en: "Close yield (%)" },
    "待偿期": { zh: "待偿期（年）", en: "Maturity (years)" },
    "交易量(亿元)": { zh: "交易量(亿元)", en: "Volume (100M)" },
    "收盘净价(元)": { zh: "收盘净价(元)", en: "Close net price" },
    "债券代码": { zh: "债券代码", en: "Bond code" },
  };

  const fieldLabel = (field) => {
    const entry = MISSING_FIELD_LABELS[field];
    if (entry) return lang() === "en" ? entry.en : entry.zh;
    return field;
  };

  function renderStatus() {
    if (!el.status) return;
    el.status.classList.remove("err");
    el.status.removeAttribute("title");
    el.status.replaceChildren();
    if (state.activeSource) {
      const source = state.activeSource;
      const runtime = runtimeInfo(source);
      const tag = document.createElement("span");
      tag.className = `mode-tag ${runtime.tag}`;
      tag.textContent = runtime.label;
      const parts = [sourceShortName(source), `${t("行情", "Quote")} ${quoteWhen(source, true)}`];
      if (source.fetched_at) parts.push(`${t("获取", "Fetched")} ${fmtFetchedAt(source.fetched_at)}`);
      const description = document.createElement("span");
      description.className = "source-meta-text";
      description.textContent = parts.join(" · ");
      el.status.append(tag, description);
      if (source.fallback_reason) {
        const fallback = document.createElement("span");
        fallback.className = "source-fallback-note";
        fallback.textContent = t("实时获取失败，已回退", "Live fetch failed; fallback active");
        el.status.append(fallback);
        el.status.title = source.fallback_reason;
      }
    } else if (state.sourceError) {
      el.status.classList.add("err");
      const message = document.createElement("span");
      message.textContent = state.importId
        ? t("上传数据不可用或已过期，请重新导入。", "Uploaded data is unavailable or expired; please import again.")
        : state.mode === "live"
          ? t("实时数据不可用，请重试。", "Live data unavailable; please retry.")
          : t("数据加载失败或超时。请重试或切换数据源。", "Data loading failed or timed out. Retry or switch data source.");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "ghost-button";
      retry.setAttribute("data-source-retry", "");
      retry.textContent = t("重试", "Retry");
      retry.addEventListener("click", () => {
        state.sourceError = null;
        state.activeSource = null;
        renderStatus();
        if (state.render) state.render();
      });
      el.status.append(message, retry);
    } else if (state.importId) {
      const report = state.report || {};
      const text = document.createElement("span");
      text.textContent = t(`已启用上传样本「${report.file_name || ""}」`, `Using uploaded sample "${report.file_name || ""}"`);
      const tag = document.createElement("span");
      tag.className = "mode-tag import-tag";
      tag.textContent = t("上传样本", "Import");
      el.status.append(text, tag);
    } else {
      el.status.textContent = state.mode === "static"
        ? t("正在读取历史样例…", "Loading historical sample…")
        : t("正在获取行情…", "Fetching quotes…");
    }
  }

  function setActiveSource(source) {
    state.activeSource = source || null;
    state.sourceError = null;
    renderStatus();
  }

  function setSourceError(error) {
    state.activeSource = null;
    state.sourceError = String(error || "");
    renderStatus();
  }

  function renderImportReport() {
    if (!el.report) return;
    const report = state.report;
    if (!report) { el.report.hidden = true; el.report.innerHTML = ""; return; }
    el.report.hidden = false;
    el.report.innerHTML = "";

    const title = document.createElement("h3");
    title.textContent = t("字段映射报告", "Column mapping report");
    el.report.append(title);

    const meta = document.createElement("p");
    meta.className = "report-meta";
    const metaParts = [
      t(`行数 ${report.row_count ?? "—"}`, `Rows ${report.row_count ?? "—"}`),
      t(`债券 ${report.bond_count ?? "—"} 只`, `${report.bond_count ?? "—"} bonds`),
      report.valid_yield_count !== undefined
        ? t(`有效收益率 ${report.valid_yield_count} 条`, `Valid yields ${report.valid_yield_count}`)
        : "",
    ].filter(Boolean);
    if (report.mixed_timepoints) {
      metaParts.push(t(
        `混合时点（${report.distinct_date_count ?? "多"} 个日期）`,
        `Mixed timepoints (${report.distinct_date_count ?? "multiple"} dates)`
      ));
    } else if (report.sample_date) {
      metaParts.push(t(`样本日期 ${report.sample_date}`, `Sample date ${report.sample_date}`));
    } else {
      metaParts.push(t("样本日期未知", "Sample date unknown"));
    }
    meta.textContent = metaParts.join(" · ");
    el.report.append(meta);

    if (report.mixed_timepoints) {
      const warn = document.createElement("p");
      warn.className = "report-warn";
      warn.textContent = t(
        `样本包含多个日期（${report.quote_date_range || "范围未知"}）：跨日对比收益率水平不可直接解读。`,
        `Sample spans multiple dates (${report.quote_date_range || "range unknown"}): cross-date yield comparisons are not directly interpretable.`
      );
      el.report.append(warn);
    }

    const fold = document.createElement("details");
    fold.className = "import-report-details";
    const summary = document.createElement("summary");
    const briefBits = [];
    const missingCount = (report.missing_fields || []).length;
    if (missingCount) {
      briefBits.push(t(`缺少 ${missingCount} 个字段，可在此选择来源列`, `${missingCount} field(s) missing; select source columns here`));
    }
    if ((report.unsupported_columns || []).length) {
      briefBits.push(t(
        `已拒绝 ${report.unsupported_columns.length} 个无法识别的列`,
        `${report.unsupported_columns.length} unrecognized column(s) rejected`
      ));
    }
    summary.textContent = briefBits.length
      ? `${t("字段映射详情", "Column-mapping detail")} — ${briefBits.join("；")}`
      : t("字段映射详情", "Column-mapping detail");
    fold.append(summary);

    const table = document.createElement("table");
    table.className = "wb-table";
    const thead = document.createElement("thead");
    thead.innerHTML = `<tr><th>${t("标准字段", "Canonical field")}</th><th>${t("来源列", "Source column")}</th><th>${t("状态", "Status")}</th></tr>`;
    table.append(thead);
    const tbody = document.createElement("tbody");
    const mapped = report.mapped_columns || {};
    const missing = report.missing_fields || [];
    const unsupported = new Set(report.unsupported_columns || []);
    const candidates = (report.unmapped_columns || []).filter((column) => !unsupported.has(column));

    Object.entries(mapped).forEach(([field, column]) => {
      const row = document.createElement("tr");
      row.innerHTML = `<td>${fieldLabel(field)}</td><td>${column}</td><td><span class="wb-badge good">${t("已映射", "Mapped")}</span></td>`;
      tbody.append(row);
    });
    missing.forEach((field) => {
      const row = document.createElement("tr");
      row.className = "field-mapping-row";
      row.dataset.field = field;
      const fieldCell = document.createElement("td");
      fieldCell.textContent = fieldLabel(field);
      const sourceCell = document.createElement("td");
      if (candidates.length) {
        const select = document.createElement("select");
        select.dataset.target = field;
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = t("（不映射）", "(not mapped)");
        select.append(emptyOption);
        candidates.forEach((column) => {
          const option = document.createElement("option");
          option.value = column;
          option.textContent = column;
          select.append(option);
        });
        sourceCell.append(select);
      } else {
        sourceCell.textContent = "—";
      }
      const statusCell = document.createElement("td");
      statusCell.innerHTML = `<span class="wb-badge warn">${t("缺失", "Missing")}</span>`;
      row.append(fieldCell, sourceCell, statusCell);
      tbody.append(row);
    });
    (report.unsupported_columns || []).forEach((column) => {
      const row = document.createElement("tr");
      row.innerHTML = `<td>—</td><td>${column}</td><td><span class="wb-badge bad">${t("拒绝映射", "Rejected")}</span></td>`;
      tbody.append(row);
    });
    table.append(tbody);
    fold.append(table);

    const applyRow = document.createElement("div");
    const applyButton = document.createElement("button");
    applyButton.type = "button";
    applyButton.className = "wb-button secondary";
    applyButton.dataset.applyOverrides = "true";
    applyButton.textContent = t("按所选映射重新解析", "Re-parse with selected mappings");
    applyButton.hidden = missing.length === 0 || candidates.length === 0;
    applyRow.append(applyButton);
    fold.append(applyRow);

    const notesList = document.createElement("ul");
    notesList.className = "report-notes";
    const notes = (lang() === "en" ? report.notes_en : report.notes_zh) || [];
    notes.forEach((note) => {
      const item = document.createElement("li");
      item.textContent = note;
      notesList.append(item);
    });
    if (notes.length) fold.append(notesList);
    el.report.append(fold);
  }

  function renderImportError(message) {
    if (!el.report) return;
    el.report.hidden = false;
    el.report.innerHTML = "";
    const box = document.createElement("p");
    box.className = "report-error";
    box.textContent = message;
    el.report.append(box);
  }

  function setImportActive(active) {
    if (!el.bar) return;
    if (el.file) el.file.disabled = active;
    if (el.upload) el.upload.disabled = active || !state.file;
    if (el.mode) el.mode.disabled = active;
    if (el.clear) el.clear.hidden = !active;
  }

  async function uploadFile(overrides) {
    if (!state.file) return;
    if (el.upload) el.upload.disabled = true;
    if (el.status) {
      el.status.classList.remove("err");
      el.status.textContent = t("正在上传并解析…", "Uploading and parsing…");
    }
    const body = new FormData();
    body.append("file", state.file);
    if (overrides && Object.keys(overrides).length) {
      body.append("column_overrides", JSON.stringify(overrides));
    }
    try {
      const payload = await fetchJson("/api/import", { method: "POST", body });
      state.importId = payload.import_id || "";
      window.bondlensSetSource?.(state.mode, state.importId);
      state.report = payload.report || null;
      state.source = payload.source || null;
      state.activeSource = null;
      state.sourceError = null;
      renderImportReport();
      renderStatus();
      setImportActive(true);
      if (state.render) state.render();
    } catch (error) {
      renderImportError(`${t("上传失败：", "Upload failed: ")}${error.message || error}`);
      if (el.status) {
        el.status.classList.add("err");
        el.status.textContent = t("上传失败，样本未启用", "Upload failed; sample not enabled");
      }
      if (el.upload) el.upload.disabled = !state.file;
    }
  }

  function readOverrides() {
    const overrides = {};
    el.report.querySelectorAll("select[data-target]").forEach((select) => {
      if (select.value) overrides[select.dataset.target] = select.value;
    });
    return overrides;
  }

  function wireSourceBar() {
    if (!collect()) return;
    const initial = new URLSearchParams(window.location.search);
    state.mode = el.mode ? el.mode.value : "auto";
    state.importId = initial.get("import_id") || "";
    setImportActive(Boolean(state.importId));
    window.bondlensSetSource?.(state.mode, state.importId);
    el.mode.addEventListener("change", () => {
      state.mode = el.mode.value;
      window.bondlensSetSource?.(state.mode, state.importId);
      state.activeSource = null;
      state.sourceError = null;
      renderStatus();
      if (state.render) state.render();
    });
    el.file.addEventListener("change", () => {
      state.file = el.file.files && el.file.files.length ? el.file.files[0] : null;
      el.upload.disabled = !state.file || el.file.disabled;
    });
    el.upload.addEventListener("click", () => uploadFile());
    el.report.addEventListener("click", (event) => {
      const button = event.target.closest("[data-apply-overrides]");
      if (button) uploadFile(readOverrides());
    });
    el.clear.addEventListener("click", () => {
      state.importId = "";
      window.bondlensSetSource?.(state.mode, "");
      state.report = null;
      state.source = null;
      state.activeSource = null;
      state.sourceError = null;
      state.file = null;
      el.file.value = "";
      el.report.hidden = true;
      el.report.innerHTML = "";
      renderStatus();
      setImportActive(false);
      if (state.mode === "import") state.mode = "auto";
      if (state.render) state.render();
    });
    renderStatus();
  }

  function init(options) {
    state.render = options && options.render ? options.render : null;
    wireSourceBar();
    const syncSource = () => window.bondlensSetSource?.(state.mode, state.importId);
    if (window.bondlensSetSource) syncSource();
    else document.addEventListener("DOMContentLoaded", syncSource, { once: true });
    document.addEventListener("bondlens:language-change", () => {
      renderStatus();
      if (state.report) renderImportReport();
      if (state.render) state.render();
    });
    if (state.render) state.render();
  }

  window.BondLensWorkbench = {
    init, t, lang, fmt, fmtDate, fmtFetchedAt, quoteWhen, runtimeInfo,
    sourceShortName, escapeHtml, setActiveSource, setSourceError,
    params, paramObject, fetchJson, state,
  };
})();

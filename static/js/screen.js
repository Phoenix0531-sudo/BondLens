/* 债券筛选 page renderer: filters → /api/bonds/search, sortable table, pagination, row → bond page. */
(() => {
  const form = document.querySelector("[data-screen-filters]");
  const results = document.querySelector("[data-screen-results]");
  if (!form || !results) return;
  const WB = window.BondLensWorkbench;

  const PAGE_SIZE = 25;
  const state = { page: 1, sort: "yield", direction: "desc", loadedTypes: false, loadSeq: 0, sourceKey: "" };
  let gridInstance = null;

  // Tabulator's collapse formatter renders a div; expose it as a keyboard control
  // without replacing its internal toggle implementation.
  function updateCollapseControls(root) {
    root.querySelectorAll('.tabulator-responsive-collapse-toggle').forEach((toggle) => {
      toggle.setAttribute('role', 'button');
      toggle.setAttribute('tabindex', '0');
      const open = toggle.classList.contains('open');
      toggle.setAttribute('aria-label', open ? WB.t('收起更多字段', 'Hide extra fields') : WB.t('展开更多字段', 'Show more fields'));
      toggle.setAttribute('aria-expanded', String(open));
    });
  }
  const collapseObserver = new MutationObserver(() => updateCollapseControls(results));
  collapseObserver.observe(results, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  results.addEventListener('keydown', (event) => {
    const toggle = event.target.closest('.tabulator-responsive-collapse-toggle');
    if (toggle && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      toggle.click();
    }
  });
  const PRESETS = {
    all: { zh: "全部债券", en: "All bonds", filters: {} },
    government: { zh: "国债", en: "Treasuries", filters: { bond_type: "国债" } },
    credit: { zh: "信用债", en: "Credit bonds", filters: { bond_type: "信用债" } },
    high_yield: { zh: "高收益债券", en: "High-yield bonds", filters: { min_yield: "4" } },
    short_term: { zh: "短期限债券", en: "Short-term bonds", filters: { max_maturity: "1" } },
    custom: { zh: "自定义筛选", en: "Custom screen", filters: {} },
  };
  const viewTitle = document.querySelector("[data-screen-view-title]");

  function selectPreset(key) {
    const selected = PRESETS[key] || PRESETS.custom;
    document.querySelectorAll("[data-screen-preset]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.screenPreset === key));
    });
    if (viewTitle) {
      viewTitle.dataset.langZh = selected.zh;
      viewTitle.dataset.langEn = selected.en;
      viewTitle.textContent = WB.lang() === "zh" ? selected.zh : selected.en;
    }
  }

  function applyPreset(key) {
    const preset = PRESETS[key];
    if (!preset || key === "custom") return;
    form.querySelectorAll("[data-filter]").forEach((input) => {
      input.value = preset.filters[input.dataset.filter] || "";
    });
    selectPreset(key);
    state.page = 1;
    load();
  }

  const COLUMNS = [
    { key: "name", field: "name", labelZh: "债券", labelEn: "Bond", numeric: false, value: (record) => record.name },
    { key: "type", field: "type_value", labelZh: "券种", labelEn: "Type", numeric: false, sortable: false, value: (record) => record.type_value },
    { key: "maturity", field: "maturity_value", labelZh: "待偿期", labelEn: "Maturity", numeric: true, value: (record) => record.maturity_value ?? "—" },
    { key: "yield", field: "yield_value", labelZh: "收益率(%)", labelEn: "Yield(%)", numeric: true, value: (record) => WB.fmt(record.yield_value, 4) },
    { key: "volume", field: "volume_value", labelZh: "成交量(亿元)", labelEn: "Volume(100M)", numeric: true, value: (record) => WB.fmt(record.volume_value, 2) },
    { key: "price", field: "收盘净价(元)", labelZh: "收盘净价(元)", labelEn: "Net price", numeric: true, sortable: true, value: (record) => WB.fmt(record["收盘净价(元)"], 2) },
  ];

  function bondHref(record) {
    const key = record["债券代码"] || record.name || "";
    const query = WB.params();
    return `/bond/${encodeURIComponent(key)}?${query.toString()}`;
  }

  function filterValues() {
    const values = {};
    form.querySelectorAll("[data-filter]").forEach((input) => {
      const raw = (input.value || "").trim();
      if (raw) values[input.dataset.filter] = raw;
    });
    return values;
  }

  async function load() {
    const seq = ++state.loadSeq;
    const sourceKey = `${WB.state.mode}|${WB.state.importId}`;
    if (sourceKey !== state.sourceKey) {
      if (state.sourceKey) selectPreset("custom");
      state.sourceKey = sourceKey;
      state.loadedTypes = false;
      state.page = 1;
      const typeSelect = form.querySelector('[data-filter="bond_type"]');
      if (typeSelect) typeSelect.value = "";
    }
    const query = WB.params();
    const values = filterValues();
    Object.entries(values).forEach(([key, value]) => query.set(key, value));
    query.set("sort", state.sort);
    query.set("direction", state.direction);
    query.set("page", String(state.page));
    query.set("page_size", String(PAGE_SIZE));

    if (gridInstance) {
      gridInstance.destroy();
      gridInstance = null;
    }
    results.setAttribute("aria-busy", "true");
    results.innerHTML = `<div class="wb-panel-body"><p class="wb-loading">${WB.t("正在加载…", "Loading…")}</p></div>`;
    let payload;
    try {
      payload = await WB.fetchJson(`/api/bonds/search?${query.toString()}`);
    } catch (error) {
      if (seq !== state.loadSeq) return; // a newer request superseded this one
      WB.setSourceError(error.message || error);
      results.removeAttribute("aria-busy");
      results.innerHTML = `<div class="wb-panel-body"><div class="wb-error-box">${WB.escapeHtml(error.message || error)}</div></div>`;
      return;
    }
    if (seq !== state.loadSeq) return; // a newer request superseded this one
    WB.setActiveSource(payload.data_source);
    results.removeAttribute("aria-busy");
    render(payload);
  }

  function render(payload) {
    const zh = WB.lang() === "zh";
    if (gridInstance) {
      gridInstance.destroy();
      gridInstance = null;
    }
    results.innerHTML = "";

    const header = document.createElement("div");
    header.className = "wb-panel-header bl-results-header";
    const countText = zh
      ? `${WB.fmt(payload.match_count, 0)} 条记录 · ${WB.fmt(payload.distinct_bond_count ?? payload.match_count, 0)} 只债券`
      : `${WB.fmt(payload.match_count, 0)} rows · ${WB.fmt(payload.distinct_bond_count ?? payload.match_count, 0)} bonds`;
    header.innerHTML = `<div class="bl-results-heading">
            <h2 class="wb-panel-title">${zh ? "概览" : "Overview"}</h2>
            <span class="bl-results-count">${countText}</span>
        </div>`;
    if (payload.sort_truncated) {
      const badge = document.createElement("span");
      badge.className = "wb-badge warn";
      badge.textContent = zh ? "仅前 5000 条参与排序分页" : "Only first 5000 sorted/paged";
      header.append(badge);
    }
    results.append(header);

    /* Tabulator is vendored and MIT-licensed. The backend still owns sorting
       and paging; the grid only renders the 25 returned records. */
    const wrap = document.createElement("div");
    wrap.className = "wb-table-wrap";
    const host = document.createElement("div");
    host.className = "bond-grid";
    host.setAttribute("aria-label", zh ? "债券筛选结果" : "Bond screening results");
    wrap.append(host);
    results.append(wrap);
    const records = payload.records || [];
    const sortBy = (column) => {
      if (state.sort === column.key) {
        state.direction = state.direction === "asc" ? "desc" : "asc";
      } else {
        state.sort = column.key;
        state.direction = column.numeric ? "desc" : "asc";
      }
      state.page = 1;
      load();
    };
    if (records.length && window.Tabulator) {
      gridInstance = new window.Tabulator(host, {
        data: records,
        layout: "fitColumns",
        responsiveLayout: "collapse",
        responsiveLayoutCollapseStartOpen: false,
        rowHeader: {
          formatter: "responsiveCollapse",
          width: 34,
          minWidth: 34,
          hozAlign: "center",
          headerSort: false,
          resizable: false,
        },
        placeholder: zh ? "没有符合筛选条件的记录。" : "No records match the filters.",
        columnDefaults: { resizable: false, headerSort: false },
        columns: COLUMNS.map((column, index) => ({
          title: `${zh ? column.labelZh : column.labelEn}${state.sort === column.key ? (state.direction === "asc" ? " ↑" : " ↓") : ""}`,
          field: column.field,
          minWidth: index === 0 ? 178 : (column.key === "type" ? 95 : 112),
          widthGrow: index === 0 ? 2 : 1,
          responsive: index === 0 ? 0 : (index === 3 ? 1 : index + 1),
          hozAlign: column.numeric ? "right" : "left",
          headerHozAlign: column.numeric ? "right" : "left",
          headerClick: column.sortable === false ? undefined : () => sortBy(column),
          formatter: (cell) => {
            const record = cell.getRow().getData();
            if (index === 0) {
              const anchor = document.createElement("a");
              anchor.className = "row-anchor";
              anchor.href = bondHref(record);
              anchor.textContent = record.name || "—";
              return anchor;
            }
            const span = document.createElement("span");
            span.textContent = column.value(record) ?? "—";
            return span;
          },
        })),
      });
      gridInstance.on("rowClick", (event, row) => {
        if (event.target.closest("a, .tabulator-row-header, .tabulator-responsive-collapse-toggle, .tabulator-responsive-collapse")) return;
        window.location.href = bondHref(row.getData());
      });
    } else {
      host.className = "wb-empty";
      host.textContent = !records.length
        ? (zh ? "没有符合筛选条件的记录。" : "No records match the filters.")
        : (zh ? "表格组件未能加载，请刷新页面。" : "Table component unavailable; please refresh.");
      if (!records.length) {
        const reset = document.createElement("button");
        reset.type = "button";
        reset.className = "wb-button secondary";
        reset.textContent = zh ? "重置筛选" : "Reset filters";
        reset.addEventListener("click", () => form.reset());
        host.append(document.createTextNode(" "), reset);
      }
    }

    /* Notes */
    const notes = [...(payload.notes || [])];
    if (payload.ambiguity_note) notes.push(payload.ambiguity_note);
    if (notes.length) {
      const noteList = document.createElement("ul");
      noteList.className = "wb-notes";
      notes.forEach((note) => {
        const item = document.createElement("li");
        item.textContent = note;
        noteList.append(item);
      });
      results.append(noteList);
    }

    /* Pagination */
    const pagination = document.createElement("div");
    pagination.className = "wb-pagination";
    const page = payload.page || 1;
    const pageCount = payload.page_count || 1;
    const prev = document.createElement("button");
    prev.type = "button";
    prev.textContent = zh ? "上一页" : "Prev";
    prev.disabled = page <= 1;
    prev.addEventListener("click", () => { state.page = page - 1; load(); });
    const next = document.createElement("button");
    next.type = "button";
    next.textContent = zh ? "下一页" : "Next";
    next.disabled = page >= pageCount;
    next.addEventListener("click", () => { state.page = page + 1; load(); });
    const label = document.createElement("span");
    label.textContent = zh ? `第 ${page} / ${pageCount} 页` : `Page ${page} of ${pageCount}`;
    pagination.append(prev, next, label);
    results.append(pagination);

    /* Available filters: populate type select once; disable unsupported inputs. */
    const available = payload.available_filters || {};
    const typeSelect = form.querySelector('[data-filter="bond_type"]');
    if (typeSelect && !state.loadedTypes) {
      state.loadedTypes = true;
      const current = typeSelect.value;
      typeSelect.innerHTML = `<option value="" data-lang-zh="全部" data-lang-en="All">${zh ? "全部" : "All"}</option>`;
      (available.bond_types || []).forEach((entry) => {
        const option = document.createElement("option");
        option.value = entry.bond_type;
        option.textContent = `${entry.bond_type} (${WB.fmt(entry.count, 0)})`;
        typeSelect.append(option);
      });
      typeSelect.value = current;
    }
    const supportedTypes = new Set((available.bond_types || []).map((entry) => entry.bond_type));
    document.querySelectorAll("[data-screen-preset]").forEach((button) => {
      const preset = button.dataset.screenPreset;
      const unsupported = (preset === "government" && !supportedTypes.has("国债")) ||
        (preset === "credit" && !supportedTypes.has("信用债")) ||
        (preset === "high_yield" && available.yield === false) ||
        (preset === "short_term" && available.maturity === false);
      button.disabled = unsupported;
    });
    [["min_maturity", "max_maturity", "maturity"], ["min_yield", "max_yield", "yield"], ["min_volume", null, "volume"]].forEach(([low, high, capability]) => {
      [low, high].forEach((name) => {
        if (!name) return;
        const input = form.querySelector(`[data-filter="${name}"]`);
        if (input) {
          const usable = available[capability] !== false;
          input.disabled = !usable;
          input.title = usable ? "" : (zh ? "当前样本缺少该列" : "Column missing in this sample");
        }
      });
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    state.page = 1;
    load();
  });
  form.addEventListener("reset", () => {
    selectPreset("all");
    state.page = 1;
    setTimeout(load, 0);
  });
  form.querySelectorAll("[data-filter]").forEach((input) => {
    input.addEventListener("input", () => selectPreset("custom"));
    input.addEventListener("change", () => selectPreset("custom"));
  });
  document.querySelectorAll("[data-screen-preset]").forEach((button) => {
    button.addEventListener("click", () => applyPreset(button.dataset.screenPreset));
  });

  WB.init({ render: load });
})();

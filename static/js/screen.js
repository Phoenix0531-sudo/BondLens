/* 债券筛选 page renderer: filters → /api/bonds/search, sortable table, pagination, row → bond page. */
(() => {
  const form = document.querySelector("[data-screen-filters]");
  const results = document.querySelector("[data-screen-results]");
  if (!form || !results) return;
  const WB = window.BondLensWorkbench;

  const PAGE_SIZE = 25;
  const state = { page: 1, sort: "yield", direction: "desc", loadedTypes: false, loadSeq: 0 };

  const COLUMNS = [
    { key: "name", field: "name", labelZh: "债券", labelEn: "Bond", numeric: false, value: (record) => record.name },
    { key: "type", field: "type_value", labelZh: "券种", labelEn: "Type", numeric: false, sortable: false, value: (record) => record.type_value },
    { key: "maturity", field: "maturity_value", labelZh: "待偿期", labelEn: "Maturity", numeric: true, value: (record) => `${record.maturity_value ?? "—"}${record["待偿期(年)"] != null ? ` (${WB.fmt(record["待偿期(年)"], 2)}y)` : ""}` },
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
    const query = WB.params();
    const values = filterValues();
    Object.entries(values).forEach(([key, value]) => query.set(key, value));
    query.set("sort", state.sort);
    query.set("direction", state.direction);
    query.set("page", String(state.page));
    query.set("page_size", String(PAGE_SIZE));

    const body = results.querySelector(".wb-panel-body");
    const existing = results.querySelector("table");
    if (!existing && body) {
      body.innerHTML = `<p class="wb-loading">${WB.t("正在加载…", "Loading…")}</p>`;
    }
    let payload;
    try {
      payload = await WB.fetchJson(`/api/bonds/search?${query.toString()}`);
    } catch (error) {
      if (seq !== state.loadSeq) return; // a newer request superseded this one
      results.innerHTML = `<div class="wb-panel-body"><div class="wb-error-box">${error.message || error}</div></div>`;
      return;
    }
    if (seq !== state.loadSeq) return; // a newer request superseded this one
    render(payload);
  }

  function render(payload) {
    const zh = WB.lang() === "zh";
    results.innerHTML = "";

    const header = document.createElement("div");
    header.className = "wb-panel-header";
    const countText = zh
      ? `命中 ${WB.fmt(payload.match_count, 0)} 条记录 / ${WB.fmt(payload.distinct_bond_count ?? payload.match_count, 0)} 只债券`
      : `${WB.fmt(payload.match_count, 0)} rows / ${WB.fmt(payload.distinct_bond_count ?? payload.match_count, 0)} bonds`;
    header.innerHTML = `<div>
            <h2 class="wb-panel-title">${countText}</h2>
            <p class="wb-panel-subtitle">${zh ? "点击行进入单券研究。" : "Click a row to open single-bond research."}</p>
        </div>`;
    if (payload.sort_truncated) {
      const badge = document.createElement("span");
      badge.className = "wb-badge warn";
      badge.textContent = zh ? "仅前 5000 条参与排序分页" : "Only first 5000 sorted/paged";
      header.append(badge);
    }
    results.append(header);

    /* Table */
    const wrap = document.createElement("div");
    wrap.className = "wb-table-wrap";
    const table = document.createElement("table");
    table.className = "wb-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    COLUMNS.forEach((column) => {
      const th = document.createElement("th");
      if (column.sortable !== false && column.key !== "type") {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "wb-sort";
        button.dataset.sortKey = column.key;
        const arrow = state.sort === column.key
          ? `<span class="arrow">${state.direction === "asc" ? "↑" : "↓"}</span>`
          : `<span class="arrow" style="visibility:hidden">↓</span>`;
        button.innerHTML = `${zh ? column.labelZh : column.labelEn} ${arrow}`;
        button.setAttribute("aria-label", `${zh ? "按" : "Sort by "}${zh ? column.labelZh : column.labelEn}`);
        button.addEventListener("click", () => {
          if (state.sort === column.key) {
            state.direction = state.direction === "asc" ? "desc" : "asc";
          } else {
            state.sort = column.key;
            state.direction = column.numeric ? "desc" : "asc";
          }
          state.page = 1;
          load();
        });
        th.append(button);
      } else {
        th.textContent = zh ? column.labelZh : column.labelEn;
        th.classList.add("num");
      }
      headRow.append(th);
    });
    thead.append(headRow);
    table.append(thead);

    const tbody = document.createElement("tbody");
    const records = payload.records || [];
    if (!records.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = COLUMNS.length;
      cell.className = "wb-empty";
      cell.textContent = zh ? "没有符合筛选条件的记录。" : "No records match the filters.";
      row.append(cell);
      tbody.append(row);
    }
    records.forEach((record) => {
      const row = document.createElement("tr");
      row.className = "row-link";
      const anchor = document.createElement("a");
      anchor.className = "row-anchor";
      anchor.href = bondHref(record);
      anchor.textContent = record.name || "—";
      const nameCell = document.createElement("td");
      nameCell.append(anchor);
      row.append(nameCell);
      COLUMNS.slice(1).forEach((column) => {
        const cell = document.createElement("td");
        cell.textContent = column.value(record) || "—";
        if (column.numeric) cell.classList.add("num");
        row.append(cell);
      });
      row.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        window.location.href = bondHref(record);
      });
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          window.location.href = bondHref(record);
        }
      });
      tbody.append(row);
    });
    table.append(tbody);
    wrap.append(table);
    results.append(wrap);

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
      typeSelect.innerHTML = `<option value="">${zh ? "全部" : "All"}</option>`;
      (available.bond_types || []).forEach((entry) => {
        const option = document.createElement("option");
        option.value = entry.bond_type;
        option.textContent = `${entry.bond_type} (${WB.fmt(entry.count, 0)})`;
        typeSelect.append(option);
      });
      typeSelect.value = current;
    }
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
    state.page = 1;
    setTimeout(load, 0);
  });

  WB.init({ render: load });
})();

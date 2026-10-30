(() => {
  const root = document.querySelector('[data-replay-workbench]');
  if (!root) return;
  const rows = [...root.querySelectorAll('[data-replay-row]')];
  const body = root.querySelector('tbody');
  const search = root.querySelector('[data-replay-search]');
  const time = root.querySelector('[data-replay-time]');
  const source = root.querySelector('[data-replay-source]');
  const status = root.querySelector('[data-replay-status]');
  const sort = root.querySelector('[data-replay-sort]');
  const prev = root.querySelector('[data-replay-prev]');
  const next = root.querySelector('[data-replay-next]');
  const pageLabel = root.querySelector('[data-replay-page]');
  let page = 1;
  function render() {
    const en = document.documentElement.lang.startsWith('en');
    const query = search.value.trim().toLocaleLowerCase();
    const cutoff = time.value ? Date.now() - Number(time.value) * 86400000 : null;
    const matched = rows.filter((row) => {
      const stamp = Date.parse(row.dataset.created);
      return row.dataset.question.toLocaleLowerCase().includes(query) &&
        (!source.value || row.dataset.source === source.value) &&
        (!status.value || row.dataset.status === status.value) &&
        (!cutoff || (Number.isFinite(stamp) && stamp >= cutoff));
    });
    matched.sort((a, b) => {
      const av = Date.parse(a.dataset.created), bv = Date.parse(b.dataset.created);
      if (!Number.isFinite(av)) return Number.isFinite(bv) ? 1 : 0;
      if (!Number.isFinite(bv)) return -1;
      return sort.value === 'asc' ? av - bv : bv - av;
    });
    const pages = Math.max(1, Math.ceil(matched.length / 20));
    page = Math.min(page, pages);
    rows.forEach((row) => { row.hidden = true; });
    matched.forEach((row, i) => { body.append(row); row.hidden = i < (page - 1) * 20 || i >= page * 20; });
    root.querySelector('[data-replay-empty]').hidden = matched.length > 0;
    pageLabel.textContent = en ? `${matched.length} results · page ${page} / ${pages}` : `${matched.length} 条结果 · 第 ${page} / ${pages} 页`;
    prev.disabled = page === 1;
    next.disabled = page === pages;
  }
  rows.forEach((row) => {
    const stamp = Date.parse(row.dataset.created);
    if (Number.isFinite(stamp)) {
      const date = row.querySelector('[data-replay-date]');
      date.textContent = new Intl.DateTimeFormat(undefined, {dateStyle:'short', timeStyle:'short', timeZoneName: undefined}).format(stamp);
      date.title = `${row.dataset.created} · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
    }
  });
  [search, time, source, status, sort].forEach((input) => input.addEventListener(input === search ? 'input' : 'change', () => { page = 1; render(); }));
  prev.addEventListener('click', () => { page--; render(); });
  next.addEventListener('click', () => { page++; render(); });
  document.addEventListener('bondlens:language-change', render);
  render();
})();

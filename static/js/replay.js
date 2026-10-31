(() => {
  const root = document.querySelector('[data-replay-workbench]');
  if (!root) return;
  root.querySelectorAll('[data-replay-row]').forEach((row) => {
    const stamp = Date.parse(row.dataset.created);
    if (Number.isFinite(stamp)) {
      const date = row.querySelector('[data-replay-date]');
      date.textContent = new Intl.DateTimeFormat(undefined, {dateStyle: 'short', timeStyle: 'short'}).format(stamp);
      date.title = `${row.dataset.created} · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
    }
  });
})();

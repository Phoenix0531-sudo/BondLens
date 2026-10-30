(() => {
  const question = document.querySelector('#question');
  const mode = document.querySelector('#data_mode');
  const note = document.querySelector('#data-mode-note');
  if (!question || !mode || !note) return;
  const update = () => {
    const en = document.documentElement.lang.startsWith('en');
    const sample = mode.value === 'static';
    note.classList.toggle('sample', sample);
    note.textContent = sample
      ? (en ? 'Sample data · not live quotes. Results describe this dataset only.' : '静态样例 · 非实时行情。分析结论仅描述当前样本。')
      : mode.value === 'live'
        ? (en ? 'Strict live only; unavailable data will not fall back.' : '严格实时模式；实时数据不可用时不会回退。')
        : (en ? 'Check the actual source and timestamp in the report. Cached or sample data may be used when live data is unavailable.' : '请以报告中的实际来源与时间为准；实时数据不可用时可能使用快照或样例。');
  };
  mode.addEventListener('change', update);
  new MutationObserver(update).observe(document.documentElement, {attributes:true, attributeFilter:['lang']});
  question.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !document.querySelector('#run-agent-button').disabled) {
      event.preventDefault(); question.form.requestSubmit();
    }
  });
  update();
})();

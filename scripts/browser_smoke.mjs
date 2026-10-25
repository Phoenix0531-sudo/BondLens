// Dependency-free headless Edge functional smoke test; screenshots are not visual approval.
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';

const root = resolve(import.meta.dirname, '..');
const temp = await mkdtemp(join(tmpdir(), 'bondlens-browser-'));
const output = process.env.BOND_BROWSER_OUTPUT || temp;
const python = process.env.BOND_BROWSER_PYTHON || join(root, '.venv/Scripts/python.exe');
const edge = process.env.BOND_BROWSER_EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const env = { ...process.env, PYTHONPATH: join(root, '.venv-bondlens/Lib/site-packages'),
  OPENAI_API_KEY: '', OPENAI_BASE_URL: '', BOND_DATA_MODE: 'static',
  BOND_REPLAY_DIR: join(temp, 'replays'), BOND_EVIDENCE_PACK_DIR: join(temp, 'packs'), PORT: '8876' };
let browser, server, socket;
const delay = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label) {
  for (let i = 0; i < 100; i++) {
    try { const value = await fn(); if (value) return value; } catch {}
    await delay(100);
  }
  throw Error(`Timed out: ${label}`);
}
try {
  server = spawn(python, ['app.py'], { cwd: root, env, stdio: 'ignore' });
  await until(async () => (await fetch('http://127.0.0.1:8876/healthz')).ok, 'local service');
  browser = spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run',
    '--remote-debugging-port=0', `--user-data-dir=${join(temp, 'profile')}`, 'about:blank'], { stdio: 'ignore' });
  const { readFile } = await import('node:fs/promises');
  const port = await until(async () => (await readFile(join(temp, 'profile/DevToolsActivePort'), 'utf8')).split('\n')[0], 'Edge CDP');
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let seq = 0; const pending = new Map(); const errors = [];
  socket.onmessage = ({data}) => {
    const m = JSON.parse(data);
    if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(Error(m.error.message)) : p.resolve(m.result); }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text);
  };
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq; pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params}));
  });
  const js = async expression => {
    const r = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const navigate = async url => { await call('Page.navigate', {url}); await until(() => js('document.readyState === "complete"'), 'page load'); };
  const click = async selector => {
    const rect = await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await call('Input.dispatchMouseEvent', {type:'mousePressed',button:'left',clickCount:1,...rect});
    await call('Input.dispatchMouseEvent', {type:'mouseReleased',button:'left',clickCount:1,...rect});
  };
  const shot = async name => { const r=await call('Page.captureScreenshot',{format:'png'}); await writeFile(join(output,name),Buffer.from(r.data,'base64')); };
  await call('Page.enable'); await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:960,height:1040,deviceScaleFactor:1,mobile:false});
  await navigate('http://127.0.0.1:8876/agent?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("#question")'),'form');
  await click('#run-agent-button');
  assert.equal(await js('document.querySelector("#question").validity.valueMissing'),true);
  await click('#question'); await call('Input.insertText',{text:'按收益率列出最高的前5只债券'});
  await click('#run-agent-button');
  await until(()=>js('document.querySelector("#agent-soft-answer").textContent.length > 30'),'SSE final result');
  assert.equal(await js('(document.querySelector("#agent-soft-answer").compareDocumentPosition(document.querySelector("#agent-soft-tech-details")) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0'),true,'final answer precedes provenance details');
  assert.equal(await js('document.querySelector("#agent-soft-source-line").textContent.length > 4'),true,'compact source line rendered');
  assert.equal(await js('document.querySelector("#run-agent-button").disabled'),false);
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'horizontal overflow');
  await shot('bondlens-960-result.png');
  await click('#agent-soft-open-link');
  await until(()=>js('location.search.includes("result_id=")'),'full result navigation');
  await until(()=>js('document.body.textContent.includes("期限补全看板")'),'report dashboard');
  assert.equal(await js('!!document.querySelector("[data-ai-explain]")'),true,'AI explain panel present');
  assert.equal(await js('!!document.querySelector("[data-ai-button]")'),true,'on-demand AI button present');
  assert.equal(await js('document.body.textContent.includes("核心结论")'),true,'core-conclusions hero');
  assert.equal(await js('document.body.textContent.includes("未请求 AI 解读")'),true,'AI-not-requested provenance');
  const pack = await js('Array.from(document.querySelectorAll("a")).map(a=>a.href).find(h=>/\\/packs\\/.*\\.html/.test(h))');
  assert.ok(pack,'HTML evidence pack link');
  await navigate(pack);
  await until(()=>js('document.body.textContent.includes("Bond")'),'evidence pack');
  // --- Local CSV import flow (Phase 1 Task E) ---
  const csv = [
    '债券简称,债券代码,到期收益率(%),待偿期,交易量(亿元)',
    '23附息国债26,230026,2.35,6.5Y,12.5',
    '23附息国债26,230026,2.36,6.5Y,3.2',
    '23附息国债09,230009,2.32,5.8Y,8.0',
    '22附息国债10,220010,2.28,7.1Y,9.5',
    '23附息国债20,230020,2.31,6.9Y,7.3',
    '24附息国债02,240002,2.29,8.0Y,6.1',
    '23国开10,230210,2.25,6.4Y,15.0',
    '23进出05,230305,2.24,6.2Y,5.5',
    '23农发30,233030,2.26,6.6Y,4.8',
    '24国开01,240201,2.20,6.0Y,11.2',
    '24进出01,240401,2.21,6.1Y,3.9',
    '17松滋金投债,1780248,5.80,3.0Y,0.4',
  ].join('\r\n');
  const csvPath = join(temp, 'import-sample.csv');
  await writeFile(csvPath, csv, 'utf8');
  await navigate('http://127.0.0.1:8876/agent?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("#import-file")'),'import panel');
  await call('DOM.enable');
  const doc = await call('DOM.getDocument');
  const fileInput = await call('DOM.querySelector', {nodeId: doc.root.nodeId, selector: '#import-file'});
  await call('DOM.setFileInputFiles', {files: [csvPath], nodeId: fileInput.nodeId});
  await click('#import-upload-button');
  await until(()=>js('document.querySelector("#import-status").textContent.includes("已切换到上传数据")'),'import active');
  assert.equal(await js('document.querySelector("#import-id").value.length > 0'),true,'import_id stored');
  assert.equal(await js('document.querySelector("#data_mode").disabled'),true,'data_mode disabled during import');
  assert.ok(await js('document.querySelector("#import-status").textContent.includes("12 行")'),'row count reported');
  await click('#question'); await call('Input.insertText',{text:'概览当前样本，并对比23附息国债26与同类债券'});
  await click('#run-agent-button');
  await until(()=>js('document.querySelector("#agent-soft-answer").textContent.length > 30'),'import-mode answer');
  assert.ok(await js('document.querySelector("#agent-soft-lineage-lines").textContent.includes("上传数据")'),'import lineage shown');
  await shot('bondlens-import-result.png');
  await click('#import-clear-button');
  await until(()=>js('document.querySelector("#import-id").value === ""'),'import cleared');
  assert.equal(await js('document.querySelector("#data_mode").disabled'),false,'data_mode re-enabled');

  // --- Phase 2 workbench: / → /market, four entries, quick ask, screen → bond, import panel ---
  await navigate('http://127.0.0.1:8876/');
  await until(()=>js('!!document.querySelector("[data-market-root] .wb-card")'),'market overview rendered');
  assert.equal(await js('location.pathname'),'/market','root redirects to market');
  const navPaths = await js('[...document.querySelectorAll(".topnav a")].map(a=>new URL(a.href).pathname)');
  for (const entry of ['/market','/screen','/bond','/replay','/agent']) assert.ok(navPaths.includes(entry),`nav entry ${entry}`);
  assert.ok(await js('document.body.textContent.includes("2024-03-22")'),'market shows sample date');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'market 960 overflow');
  await shot('bondlens-market-960.png');
  // Quick ask carries the question into the agent console.
  await click('[data-quick-question]');
  await call('Input.insertText',{text:'当前样本中收益率最高的债券是哪只'});
  await click('[data-quick-ask]');
  await until(()=>js('location.pathname === "/agent" && new URLSearchParams(location.search).get("question")'),'quick ask navigates to agent');
  await until(()=>js('document.querySelector("#question").value.length > 0'),'question prefilled');

  // Screener: filter → real rows → row click into single-bond research.
  await navigate('http://127.0.0.1:8876/screen?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("[data-screen-results] tbody tr.row-link")'),'screen rows');
  await click('[data-filter="name"]'); await call('Input.insertText',{text:'国债'});
  await click('[data-screen-filters] button[type="submit"]');
  await until(()=>js('document.querySelector("[data-screen-results] .wb-panel-title").textContent.includes("116")'),'filtered count');
  await click('[data-screen-results] tbody tr.row-link a.row-anchor');
  await until(()=>js('location.pathname.startsWith("/bond/")'),'row click opens bond page');
  await until(()=>js('!!document.querySelector("[data-bond-root] .wb-bond-identity h2")'),'bond identity rendered');
  assert.ok(await js('document.querySelector("[data-bond-root]").textContent.includes("同业对比") || document.querySelector("[data-bond-root]").textContent.includes("Peer comparison")'),'peer comparison shown');
  assert.equal(await js('!!document.querySelector("[data-ai-explain]")'),true,'bond AI explain panel present');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'bond 960 overflow');
  await shot('bondlens-bond-960.png');

  // Import via the shared workbench panel: dirty calibers rejected, mapping report visible.
  const dirtyCsv = ['债券简称,全价,成交金额','脏价债甲,101.2,5000','脏价债乙,99.8,3200'].join('\r\n');
  const dirtyPath = join(temp, 'dirty-caliber.csv');
  await writeFile(dirtyPath, dirtyCsv, 'utf8');
  await navigate('http://127.0.0.1:8876/screen?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("[data-import-file]")'),'workbench import input');
  const dirtyDoc = await call('DOM.getDocument');
  const dirtyInput = await call('DOM.querySelector', {nodeId: dirtyDoc.root.nodeId, selector: '[data-import-file]'});
  await call('DOM.setFileInputFiles', {files: [dirtyPath], nodeId: dirtyInput.nodeId});
  await click('[data-import-upload]');
  await until(()=>js('!!document.querySelector("[data-import-report] h3")'),'mapping report rendered');
  assert.ok(await js('[...document.querySelectorAll("[data-import-report] .wb-badge.bad")].some(b=>b.textContent.includes("拒绝映射"))'),'forbidden caliber badge');
  assert.ok(await js('document.querySelector("[data-import-report]").textContent.includes("应计利息")'),'accrued-interest reason');
  assert.equal(await js('[...document.querySelectorAll("[data-import-report] select option")].some(o=>["全价","成交金额"].includes(o.value))'),false,'forbidden columns not offered as mappings');
  await until(()=>js('document.querySelector("[data-source-status]").textContent.includes("上传样本")'),'import active on workbench');
  await until(()=>js('document.querySelector("[data-screen-results] .wb-panel-title").textContent.includes("2")'),'screen reflects imported sample');
  await shot('bondlens-screen-import.png');
  await click('[data-import-clear]');
  await until(()=>js('!document.querySelector("[data-import-report] h3")'),'import cleared on workbench');
  await until(()=>js('!document.querySelector("[data-source-status]").textContent.includes("上传样本")'),'status back to built-in source');
  await until(()=>js('document.querySelector("[data-screen-results] .wb-panel-title").textContent.includes("3,365")'),'screen back to the full built-in sample');

  await navigate('http://127.0.0.1:8876/agent?result_id=expired&lang=en&data_mode=static');
  await until(()=>js('document.body.textContent.includes("Run the analysis again")'),'expired result message');
  await shot('bondlens-960-expired.png');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await navigate('http://127.0.0.1:8876/agent?data_mode=static&lang=en');
  await until(()=>js('document.querySelector("#data-mode-note")?.textContent.includes("not live quotes")'),'sample warning');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'desktop overflow');
  await shot('bondlens-1440-form.png');
  await navigate('http://127.0.0.1:8876/market?data_mode=static&lang=en');
  await until(()=>js('document.body.textContent.includes("Market Overview")'),'market English heading');
  await until(()=>js('document.querySelector("[data-market-root]").textContent.includes("Yield distribution")'),'market English content rendered');
  assert.equal(await js('document.querySelector("[data-source-status]").textContent.trim() === ""'),true,'source bar deduplicated in explicit static mode');
  assert.equal(await js('!!document.querySelector(".source-summary")'),true,'compact source summary line');
  assert.equal(await js('!!document.querySelector(".source-details")'),true,'foldable source & caliber details');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'market 1440 overflow');
  await shot('bondlens-market-1440.png');
  await navigate('http://127.0.0.1:8876/replay?lang=en');
  await until(()=>js('document.body.textContent.includes("Research history")'),'history page heading');
  assert.ok(await js('document.body.textContent.includes("recent research runs") || document.body.textContent.includes("No research history yet")'),'history summary or empty state');
  if (await js('document.querySelector(".replay-card")')) {
    assert.equal(await js('!!document.querySelector(".replay-card .replay-tech")'),true,'history card technical details folded');
    assert.equal(await js('!!document.querySelector(".replay-actions a")'),true,'history card actions (re-run / evidence pack)');
  }
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'replay 1440 overflow');
  await shot('bondlens-replay-1440.png');
  assert.deepEqual(errors,[],'uncaught browser exceptions');
  console.log('PASS: 960x1040 native input/click, required field, SSE final (answer first, provenance folded), full report (core-conclusions hero + on-demand AI panel, provenance "AI not requested"), HTML pack, CSV import (upload→query→lineage→clear), expired result; workbench: / → /market, four entries, quick ask prefill, screen filter→row→bond peer comparison + bond AI panel, shared import panel with rejected calibers and correction UI, deduplicated source bar + compact source summary; 1440x1000 bilingual market page + research history page (reading cards, technical details folded); no horizontal overflow or JS exceptions.');
  console.log(`Screenshots: ${output}`);
} finally {
  socket?.close(); browser?.kill(); server?.kill();
  await delay(500);
  // Keep screenshots for review; remove only this run's isolated browser/data directories.
  await rm(join(temp,'profile'),{recursive:true,force:true}).catch(()=>{});
  await rm(join(temp,'packs'),{recursive:true,force:true});
  await rm(join(temp,'replays'),{recursive:true,force:true});
}

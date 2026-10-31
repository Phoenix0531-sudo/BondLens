// Dependency-free headless Edge functional smoke test; screenshots are not visual approval.
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
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
  // Deterministically simulate a stalled market read without touching real data or AI.
  const injected = await call('Page.addScriptToEvaluateOnNewDocument', {source: `
    (() => {
      const realFetch = window.fetch.bind(window);
      const realTimeout = window.setTimeout.bind(window);
      let first = true;
      window.setTimeout = (fn, ms, ...args) => realTimeout(fn, ms === 30000 ? 50 : ms, ...args);
      window.fetch = (url, options) => {
        if (first && String(url).includes('/api/market/overview')) {
          first = false;
          return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => { window.setTimeout = realTimeout; reject(new DOMException('Aborted', 'AbortError')); }, {once:true}));
        }
        return realFetch(url, options);
      };
    })();`});
  await navigate('http://127.0.0.1:8876/market?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("[data-source-retry]")'),'timeout retry visible');
  assert.ok(await js('document.querySelector("[data-source-status]").textContent.includes("数据加载失败")'),'visible failure');
  assert.equal(await js('document.querySelector("[data-source-status] .mode-tag") === null'),true,'no stale success source');
  await click('[data-source-retry]');
  await until(()=>js('document.querySelector("[data-market-root] .wb-card") !== null'),'retry recovers market');
  assert.ok(await js('document.querySelector("[data-source-status]").textContent.includes("2024-03-22")'),'retry restores actual static date');
  await call('Page.removeScriptToEvaluateOnNewDocument',{identifier:injected.identifier});
  const hostile = await call('Page.addScriptToEvaluateOnNewDocument', {source: `
    (() => { const original = window.fetch.bind(window); window.fetch = (url, options) =>
      /\\/api\\/(market\\/overview|bonds\\/search|bond\\/detail)/.test(String(url))
        ? Promise.resolve(new Response(JSON.stringify({error:'<img src=x onerror=alert(1)> provider failure'}), {status:500, headers:{'Content-Type':'application/json'}}))
        : original(url, options); })();`});
  for (const route of ['/market', '/screen', '/bond/230026']) {
    await navigate(`http://127.0.0.1:8876${route}?data_mode=static`);
    await until(()=>js('!!document.querySelector(".wb-error-box")'),'injected error displayed');
    assert.ok(await js('document.querySelector(".wb-error-box").textContent.includes("<img src=x")'),'error displayed literally');
    assert.equal(await js('!!document.querySelector(".wb-error-box img")'),false,'error markup not interpreted');
  }
  await call('Page.removeScriptToEvaluateOnNewDocument',{identifier:hostile.identifier});
  await navigate('http://127.0.0.1:8876/not-a-real-route?lang=en');
  assert.ok(await js('document.body.textContent.includes("Page not found") && [...document.querySelectorAll(".topnav a")].some(a => new URL(a.href).pathname === "/market")'),'404 recovery navigation');
  await call('Emulation.setDeviceMetricsOverride',{width:960,height:1040,deviceScaleFactor:1,mobile:false});
  const streamFailure = await call('Page.addScriptToEvaluateOnNewDocument', {source: `
    (() => { const original = window.fetch.bind(window); window.fetch = (url, options) =>
      String(url).includes('/api/agent/stream')
        ? Promise.resolve(new Response('event: error\\ndata: {"error":"offline fixture"}\\n\\n', {headers:{'Content-Type':'text/event-stream'}}))
        : original(url, options); })();`});
  await navigate('http://127.0.0.1:8876/agent?data_mode=static&lang=en');
  await click('#question'); await call('Input.insertText',{text:'test stream failure'});
  await click('#run-agent-button');
  await until(()=>js('!document.querySelector("#agent-async-error").hidden'),'stream error');
  assert.equal(await js('document.querySelector("#agent-async-error").offsetParent !== null'),true,'stream error visible');
  assert.equal(await js('document.querySelector("#run-agent-button").disabled'),false,'retry enabled');
  await call('Page.removeScriptToEvaluateOnNewDocument',{identifier:streamFailure.identifier});
  const timeoutFixture = await call('Page.addScriptToEvaluateOnNewDocument', {source: `
    (() => { const original = window.fetch.bind(window); window.fetch = (url, options) =>
      String(url).includes('/api/agent/stream')
        ? Promise.resolve(new Response('event: error\\ndata: {"error":"Live data unavailable; please retry."}\\n\\n', {headers:{'Content-Type':'text/event-stream'}}))
        : original(url, options); })();`});
  await navigate('http://127.0.0.1:8876/agent?data_mode=live&lang=en');
  await click('#question'); await call('Input.insertText',{text:'timeout test'}); await click('#run-agent-button');
  await until(()=>js('!document.querySelector("#agent-async-error").hidden'),'timeout terminal');
  assert.ok(await js('document.querySelector("#agent-async-error").textContent.includes("Live data unavailable")'));
  assert.equal(await js('document.querySelector("#agent-async-error").offsetParent !== null && !document.querySelector("#run-agent-button").disabled'),true,'timeout visible and retry enabled');
  await call('Page.removeScriptToEvaluateOnNewDocument',{identifier:timeoutFixture.identifier});
  const stall = await call('Page.addScriptToEvaluateOnNewDocument', {source: `
    (() => { const original = window.fetch.bind(window); window.fetch = (url, options) =>
      String(url).includes('/api/agent/stream')
        ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted','AbortError')), {once:true}))
        : original(url, options); })();`});
  await navigate('http://127.0.0.1:8876/agent?data_mode=static&lang=en');
  await click('#question'); await call('Input.insertText',{text:'cancel test'});
  await click('#run-agent-button');
  await until(()=>js('!document.querySelector("#agent-cancel-button").disabled'),'cancel enabled');
  await click('#agent-cancel-button');
  await until(()=>js('!document.querySelector("#agent-async-error").hidden'),'cancel terminal');
  assert.ok(await js('document.querySelector("#agent-async-error").textContent.includes("Stopped receiving")'));
  assert.equal(await js('document.querySelector("#agent-async-error").offsetParent !== null && !document.querySelector("#run-agent-button").disabled'),true,'cancel visible and retry enabled');
  await call('Page.removeScriptToEvaluateOnNewDocument',{identifier:stall.identifier});
  await navigate('http://127.0.0.1:8876/agent?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("#question")'),'form');
  await click('#run-agent-button');
  assert.equal(await js('document.querySelector("#question").validity.valueMissing'),true);
  await click('#question'); await call('Input.insertText',{text:'按收益率列出最高的前5只债券'});
  await click('#run-agent-button');
  await until(()=>js('document.querySelector("#agent-soft-answer").textContent.length > 30'),'SSE final result');
  assert.equal(await js('(document.querySelector("#agent-soft-answer").compareDocumentPosition(document.querySelector("#agent-soft-tech-details")) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0'),true,'final answer precedes provenance details');
  assert.equal(await js('document.querySelector("#agent-soft-source-line").textContent.length > 4'),true,'compact source line rendered');
  assert.equal(await js('!document.querySelector("#agent-soft-summary").hidden && document.querySelector("#agent-soft-sentences li") !== null'),true,'summary conclusions visible');
  assert.equal(await js('!document.querySelector("#agent-soft-full-answer").open'),true,'full answer initially collapsed');
  await call('Emulation.setDeviceMetricsOverride',{width:440,height:900,deviceScaleFactor:1,mobile:false});
  await js('document.querySelector("#agent-soft-result").scrollIntoView()');
  await shot('bondlens-agent-result-440.png');
  console.log(`440 result screenshot: ${output}`);
  await call('Emulation.setDeviceMetricsOverride',{width:960,height:1040,deviceScaleFactor:1,mobile:false});
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
  await click('.agent-advanced summary');
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
  await until(()=>js('!!document.querySelector("[data-screen-results] .tabulator-row")'),'screen rows');
  await click('[data-filter="name"]'); await call('Input.insertText',{text:'国债'});
  await click('[data-screen-filters] button[type="submit"]');
  await until(()=>js('document.querySelector("[data-screen-results] .bl-results-count")?.textContent.includes("116")'),'filtered count');
  await call('Emulation.setDeviceMetricsOverride',{width:440,height:900,deviceScaleFactor:1,mobile:false});
  await until(()=>js('!!document.querySelector(".tabulator-responsive-collapse-toggle")'),'responsive collapse control');
  const collapse = '.tabulator-row .tabulator-responsive-collapse-toggle';
  await until(()=>js(`document.querySelector(${JSON.stringify(collapse)})?.getAttribute('role') === 'button'`),'collapse accessibility');
  assert.equal(await js(`document.querySelector(${JSON.stringify(collapse)}).getAttribute('tabindex')`),'0');
  await js(`document.querySelector(${JSON.stringify(collapse)}).scrollIntoView({block:'center',behavior:'instant'})`);
  await until(()=>js(`(()=>{const e=document.querySelector(${JSON.stringify(collapse)}); if(!e||!e.isConnected)return false; e.focus(); window.__collapseTest=e; return document.activeElement===e && e===document.querySelector(${JSON.stringify(collapse)});})()`),'collapse focused and attached before Enter');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await until(()=>js(`document.querySelector(${JSON.stringify(collapse)}).getAttribute('aria-expanded') === 'true'`),'collapse opens by Enter');
  await until(()=>js(`(()=>{const e=document.querySelector(${JSON.stringify(collapse)}); e.focus(); return document.activeElement===e;})()`),'collapse refocused before Space');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
  await until(()=>js(`document.querySelector(${JSON.stringify(collapse)}).getAttribute('aria-expanded') === 'false'`),'collapse closes by Space');
  for (let n = 0; n < 3; n++) {
    await until(()=>js(`(()=>{const e=document.querySelector(${JSON.stringify(collapse)}); e.scrollIntoView({block:'center',behavior:'instant'}); e.focus(); return document.activeElement===e;})()`),'repeat collapse focused');
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});
    await call('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
    await until(()=>js(`document.querySelector(${JSON.stringify(collapse)}).getAttribute('aria-expanded') === 'true'`),'repeat space opens');
    await until(()=>js(`(()=>{const e=document.querySelector(${JSON.stringify(collapse)}); e.scrollIntoView({block:'center',behavior:'instant'}); e.focus(); return document.activeElement===e;})()`),'repeat collapse refocused');
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});
    await call('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
    await until(()=>js(`document.querySelector(${JSON.stringify(collapse)}).getAttribute('aria-expanded') === 'false'`),'repeat space closes');
  }
  await call('Emulation.setDeviceMetricsOverride',{width:960,height:1040,deviceScaleFactor:1,mobile:false});
  const rowAnchor = '[data-screen-results] .tabulator-row a.row-anchor';
  await js(`document.querySelector(${JSON.stringify(rowAnchor)}).scrollIntoView({block:'center',behavior:'instant'})`);
  let previous = null;
  let point;
  try { point = await until(async () => {
    const current = await js(`(()=>{const e=document.querySelector(${JSON.stringify(rowAnchor)}); if(!e)return null; const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, hit=document.elementFromPoint(x,y); return {x,y,width:r.width,height:r.height,hit:!!hit&&(hit===e||e.contains(hit)),tag:hit?.tagName||null};})()`);
    const stable = current && previous && Math.abs(current.x-previous.x)<0.5 && Math.abs(current.y-previous.y)<0.5 && current.width>0 && current.height>0 && current.hit;
    previous = current;
    return stable ? current : null;
  }, 'stable row anchor hit target'); } catch (error) {
    throw Error(`Row anchor did not stabilize or was occluded: ${JSON.stringify(previous)}; ${error.message}`);
  }
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:point.x,y:point.y});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:point.x,y:point.y});
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
  await until(()=>js('document.querySelector("[data-screen-results] .bl-results-count")?.textContent.includes("2")'),'screen reflects imported sample');
  await shot('bondlens-screen-import.png');
  await click('[data-import-clear]');
  await until(()=>js('!document.querySelector("[data-import-report] h3")'),'import cleared on workbench');
  await until(()=>js('!document.querySelector("[data-source-status]").textContent.includes("上传样本")'),'status back to built-in source');
  await until(()=>js('document.querySelector("[data-screen-results] .bl-results-count")?.textContent.includes("3,365")'),'screen back to the full built-in sample');

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
  assert.ok(await js('document.querySelector("[data-source-status]").textContent.includes("Historical sample") && document.querySelector("[data-source-status]").textContent.includes("2024-03-22")'),'static source and quote date visible');
  assert.equal(await js('!!document.querySelector(".source-summary")'),true,'compact source summary line');
  assert.equal(await js('!!document.querySelector(".source-details")'),true,'foldable source & caliber details');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'market 1440 overflow');
  await shot('bondlens-market-1440.png');
  await mkdir(join(temp, 'replays'), {recursive:true});
  for (let i=0;i<25;i++) {
    const id=`20200101-0000${String(i).padStart(2,'0')}-12345678`;
    await writeFile(join(temp,'replays',`${id}.json`),JSON.stringify({id,created_at:'2020-01-01T00:00:00Z',question:i===0?'unique-test-needle':'fixture',data_source:{runtime_mode:'static_sample'},llm:{status:'disabled'}}));
  }
  await navigate('http://127.0.0.1:8876/replay?lang=en');
  await until(()=>js('document.body.textContent.includes("Research history")'),'history page heading');
  assert.equal(await js('document.querySelectorAll("[data-replay-row]").length'),20,'server page bounded');
  await click('[data-replay-next]');
  await until(()=>js('location.search.includes("cursor=")'),'history next page');
  assert.ok(await js('!!document.querySelector("[data-replay-prev]")'),'history previous page');
  await click('[data-replay-prev]');
  await until(()=>js('location.search.includes("direction=prev")'),'history previous navigation');
  await click('[data-replay-search]'); await call('Input.insertText',{text:'unique-test-needle'});
  await click('.replay-filters button[type=submit]');
  await until(()=>js('location.search.includes("q=unique-test-needle")'),'history search');
  assert.equal(await js('document.querySelectorAll("[data-replay-row]").length'),1,'search across all pages');
  assert.equal(await js('!!document.querySelector(".replay-table") && !!document.querySelector("[data-replay-search]")'),true,'history table search and pagination');
  if (await js('document.querySelector("[data-replay-row]")')) {
    assert.equal(await js('!!document.querySelector("[data-replay-row] details")'),true,'history details available');
    assert.equal(await js('!!document.querySelector("[data-replay-row] .replay-detail a, [data-replay-row] .replay-detail [aria-disabled]")'),true,'history actions or missing pack state');
  }
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'),true,'replay 1440 overflow');
  await shot('bondlens-replay-1440.png');
  await call('Emulation.setDeviceMetricsOverride',{width:293,height:600,deviceScaleFactor:1,mobile:false});
  for (const route of ['/market','/screen','/bond','/agent','/replay']) {
    await navigate(`http://127.0.0.1:8876${route}?data_mode=static&lang=en`);
    await until(()=>js('document.title.includes("BondLens")'),'page title');
    const layout = await js('({width:innerWidth,scroll:document.documentElement.scrollWidth,title:document.title})');
    console.log(`293 viewport ${route}: ${JSON.stringify(layout)}`);
  }
  assert.deepEqual(errors,[],'uncaught browser exceptions');
  console.log('PASS: timeout recovery, escaped error messages, 404 navigation, SSE summary and folded full answer, HTML evidence pack, CSV import and clear, expired result; five navigation entries, market source/date, screen keyboard expansion and bond navigation, import mapping, bilingual market and searchable history table; checked viewport overflow and uncaught JS exceptions.');
  console.log(`Screenshots: ${output}`);
} finally {
  socket?.close(); browser?.kill(); server?.kill();
  await delay(500);
  // Keep screenshots for review; remove only this run's isolated browser/data directories.
  await rm(join(temp,'profile'),{recursive:true,force:true}).catch(()=>{});
  await rm(join(temp,'packs'),{recursive:true,force:true});
  await rm(join(temp,'replays'),{recursive:true,force:true});
}

// Reproducible UI capture for docs/portfolio_case (actual local Edge pages).
// Usage: node scripts/capture_portfolio_case.mjs
// Uses an isolated temporary Flask instance and history, no model or live feed.
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createServer } from 'node:net';
import assert from 'node:assert/strict';

const root = resolve(import.meta.dirname, '..');
const tmp = await mkdtemp(join(tmpdir(), 'bondlens-portfolio-capture-'));
const output = join(root, 'docs', 'portfolio_case', 'screenshots');
const caseData = JSON.parse(await readFile(join(root, 'docs','portfolio_case','case-data.json'),'utf8'));
const bond = caseData.name;
const question = caseData.question;
assert.ok(bond && question && question.includes(bond), 'case-data.json must identify one target');
const python = join(root, '.venv-bondlens', 'Scripts', 'python.exe');
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const getPort = async () => {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
};
async function until(fn, label, tries = 200) {
  for (let i = 0; i < tries; i++) {
    try { const value = await fn(); if (value) return value; } catch {}
    await wait(150);
  }
  throw new Error('Timeout: ' + label);
}

let browser, server, socket;
try {
  const port = await getPort(), base = 'http://127.0.0.1:' + port;
  const env = {
    ...process.env, PORT: String(port), FLASK_ENV: 'production',
    FLASK_RUN_HOST: '127.0.0.1', BOND_DATA_MODE: 'static',
    BOND_REPLAY_ENABLED: 'true',
    BOND_EVIDENCE_PACK_ENABLED: 'true',
    BOND_REPLAY_DIR: join(tmp, 'replays'),
    BOND_EVIDENCE_PACK_DIR: join(tmp, 'packs'),
    OPENAI_API_KEY: '', OPENAI_BASE_URL: '',
  };
  await mkdir(output, {recursive: true});
  server = spawn(python, ['app.py'], {cwd:root,env,stdio:'ignore'});
  await until(async () => (await fetch(base+'/healthz')).ok,'isolated BondLens service');
  browser = spawn(edge, ['--headless=new','--disable-gpu','--no-first-run',
    '--remote-debugging-port=0','--force-device-scale-factor=1',
    '--user-data-dir='+join(tmp,'edge-profile'),'about:blank'], {stdio:'ignore'});
  const debugPort=await until(async () => (await readFile(join(tmp,'edge-profile','DevToolsActivePort'),'utf8')).split('\n')[0], 'Edge CDP');
  const pages=await (await fetch('http://127.0.0.1:'+debugPort+'/json/list')).json();
  socket=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  const requests=new Map();let seq=0;
  socket.onmessage=({data})=>{
    const msg=JSON.parse(data);
    if (!msg.id) return;
    const entry=requests.get(msg.id);if(!entry)return;
    requests.delete(msg.id);
    msg.error?entry.reject(Error(msg.error.message)):entry.resolve(msg.result);
  };
  const call=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++seq;requests.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));
  });
  const js=async expression=>{
    const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const navigate=async path=>{
    const url=path.startsWith('file:')?path:base+path;
    await call('Page.navigate',{url});
    await until(()=>js('document.readyState === "complete"'),'page loaded');
  };
  const screenshot=async (name, verify, note)=>{
    if(verify)assert.ok(await js(verify), name+' verification failed');
    const details=await js('({title:document.title,scroll:document.documentElement.scrollWidth,width:innerWidth})');
    assert.ok(details.scroll<=details.width,name+' horizontal overflow');
    await wait(320);
    const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    const path=join(output,name);const bytes=Buffer.from(r.data,'base64');
    await writeFile(path,bytes);
    assert.ok(bytes.length>15000, name+' suspiciously small');
    console.log(JSON.stringify({name,bytes:bytes.length,notes:note,title:details.title}));
  };
  await call('Page.enable');await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:920,deviceScaleFactor:1,mobile:false});
  await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:'light'}]});

  await navigate('/market?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("[data-market-root] .wb-card")'),'market');
  await screenshot('01-market.png','document.body.innerText.includes("2024-03-22")','Market: static source/date');

  await navigate('/screen?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("[data-screen-results] .tabulator-row")'),'screen');
  await js(`(()=>{
    const input=document.querySelector('[data-filter="name"]');
    input.value=${JSON.stringify(bond)};
    input.dispatchEvent(new Event('input',{bubbles:true}));
    document.querySelector('[data-screen-filters]').requestSubmit();
  })()`);
  await until(()=>js(`document.querySelector('[data-screen-results] .tabulator-row')?.innerText.includes(${JSON.stringify(bond)})`),'bond filter');
  await screenshot('02-screen.png',`document.querySelector('[data-screen-results] .tabulator-row')?.innerText.includes(${JSON.stringify(bond)})`,'Screen: exact target bond');

  await navigate('/bond/'+encodeURIComponent(bond)+'?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("[data-bond-root] .wb-bond-identity h2")'),'bond');
  await until(()=>js('document.querySelector("[data-bond-root]")?.innerText.includes("同业对比")'),'peers');
  await screenshot('03-bond.png',`document.querySelector('[data-bond-root] .wb-bond-identity h2')?.innerText.includes(${JSON.stringify(bond)})`,'Bond: name and peer comparison');

  await navigate('/agent?data_mode=static&lang=zh');
  await until(()=>js('!!document.querySelector("#question")'),'agent input');
  await js(`(()=>{
    const input=document.querySelector('#question');
    input.value=${JSON.stringify(question)};
    input.dispatchEvent(new Event('input',{bubbles:true}));
    document.querySelector('#agent-query-form').requestSubmit();
  })()`);
  await until(()=>js('document.querySelector("#agent-soft-answer")?.textContent.length > 90'),'deterministic answer',400);
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:860,deviceScaleFactor:1,mobile:false});
  await js('(()=>{document.documentElement.style.scrollBehavior="auto";const el=document.querySelector("#agent-soft-result");window.scrollTo({top:window.scrollY+el.getBoundingClientRect().top-95,behavior:"instant"});})()');
  await screenshot('04-agent.png',`document.querySelector('#agent-soft-answer')?.textContent.includes(${JSON.stringify(bond)})`,'Agent: same bond and question, no AI');

  // The actual browser run above has persisted one isolated research record.
  for (const q of [
    '市场概览：2024年3月22日静态样本',
    '筛选国债并观察收益率分布',
  ]) {
    const res=await fetch(base+'/api/agent/query',{method:'POST',
      headers:{'content-type':'application/json'},
      body:JSON.stringify({question:q,data_mode:'static',lang:'zh'})});
    assert.equal(res.status,200,'extra deterministic demo history');
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
  await navigate('/replay?lang=zh');
  await until(()=>js('document.querySelectorAll("[data-replay-row]").length>=3'),'history rows');
  await screenshot('05-history.png',`document.querySelector('[data-replay-workbench]')?.innerText.includes(${JSON.stringify(bond)})`,'History: same target study appears');

  const htmlFile=join(root,'docs','portfolio_case','bond-study.html');
  const uri='file:///'+htmlFile.replaceAll('\\','/');
  await navigate(uri);
  await until(()=>js('document.querySelector("body")?.innerText.length>100'),'static pack');
  await screenshot('06-evidence-pack.png',`document.body.innerText.includes(${JSON.stringify(bond)})`,'Evidence pack: same target in actual generated HTML');

  console.log('CASE_SCREENSHOTS_PASS: six real views; static sample; no model/live; isolated history');
} finally {
  socket?.close();browser?.kill();server?.kill();
  await wait(500);
  await rm(tmp,{recursive:true,force:true}).catch(()=>{});
}

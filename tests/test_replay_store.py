from bond_agent.replay_store import list_replays, save_replay


def test_replay_store_saves_sanitized_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("BOND_REPLAY_DIR", str(tmp_path))

    record = save_replay(
        {
            "question": "当前样本收益率分布是什么样？",
            "plan": {"intent": "market_overview"},
            "tools_used": ["describe_market", "generate_bond_report"],
            "data_source": {
                "source_id": "local_static_excel",
                "source_name": "data/testdata.xlsx",
                "runtime_mode": "static_sample",
                "row_count": 3000,
                "valid_yield_count": 2900,
            },
            "evidence_quality": {
                "score": 70,
                "level": "medium",
                "data_freshness": "static_snapshot",
                "decision_confidence": "low",
            },
            "llm_guardrail": {"status": "not_run"},
            "answer_judge": {"status": "not_applicable"},
            "risk_profile": {"overall_level": "medium", "summary_zh": "中文摘要", "summary_en": "English summary"},
            "llm_status": "disabled",
            "final_answer_source": "deterministic_fallback",
            "evidence_ledger": [{"id": "data_source"}],
        }
    )

    assert record is not None
    records = list_replays()
    assert len(records) == 1
    assert records[0]["question"] == "当前样本收益率分布是什么样？"
    assert records[0]["data_source"]["runtime_mode"] == "static_sample"
    assert "final_answer" not in records[0]


def test_full_result_restores_after_cache_loss(monkeypatch, tmp_path):
    from app import _RESULT_CACHE, app
    from bond_agent.agent import BondAnalystAgent

    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    result = BondAnalystAgent(data_mode='static').answer('概览当前市场')
    import bond_agent.replay_store as replay_store
    assert hasattr(replay_store, 'load_replay'), 'persistent replay reader missing'
    record = replay_store.load_replay(result['replay_id'])
    assert record['schema_version'] == 2
    assert record['result']['final_answer'] == result['final_answer']
    assert record['result']['data_evidence'] == result['data_evidence']
    _RESULT_CACHE.clear()
    response = app.test_client().get(f"/replay/{result['replay_id']}?lang=en")
    assert response.status_code == 200
    assert 'Saved historical result' in response.get_data(as_text=True)
    assert '概览当前市场' in response.get_data(as_text=True)


def test_corrupt_history_is_skipped_and_invalid_ids_rejected(monkeypatch, tmp_path):
    from bond_agent.replay_store import load_replay
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path))
    (tmp_path / '20260101-120000-12345678.json').write_text('{broken', encoding='utf-8')
    (tmp_path / '20260101-120001-12345678.json').write_text('{"id":"20260101-120001-12345678","llm":"bad"}', encoding='utf-8')
    assert list_replays() == []
    assert load_replay('../secret') is None


def test_sensitive_values_are_redacted_from_replay(monkeypatch, tmp_path):
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path))
    record = save_replay({'question': 'token=private-secret', 'final_answer': 'safe',
                          'data_source': {'runtime_mode': 'import', 'source_name': 'private-file.csv'},
                          'data_evidence': {'ranking': {'records': [{'债券简称': 'x', 'api_key': 'secret-value'}]}}})
    stored = (tmp_path / (record['id'] + '.json')).read_text(encoding='utf-8')
    assert 'private-secret' not in stored
    assert 'private-file.csv' not in stored
    assert 'secret-value' not in stored


def test_untrusted_nested_text_is_not_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path))
    record = save_replay({'question': '个人电话 13800138000', 'final_answer': '私人数据 13800138000',
                          'data_source': {'runtime_mode': 'import'},
                          'data_evidence': {'market': {'opaque': {'comment': '邮箱 private@example.org'}}}})
    stored = (tmp_path / (record['id'] + '.json')).read_text(encoding='utf-8')
    assert '13800138000' not in stored
    assert 'private@example.org' not in stored


def test_import_sentinel_absent_from_all_artifacts(monkeypatch, tmp_path):
    import json

    import pandas as pd

    from bond_agent.agent import BondAnalystAgent
    from bond_agent.importer import describe_import_source, prepare_import_frame
    from bond_agent.replay_store import load_replay

    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    marker = 'PRIVATE_TENANT_BRAVO'
    frame, report = prepare_import_frame(pd.DataFrame({'债券简称': [marker], '收益率': [2.13], '净价': [100]}), file_name='upload.csv')
    source = describe_import_source(frame, report)
    source['metadata'] = {'anything': marker}
    result = BondAnalystAgent(data_mode='static').answer(marker, data_frame=frame, data_source=source)
    record = load_replay(result['replay_id'])
    assert '2.13' in json.dumps(record, ensure_ascii=False)
    for directory in ('history', 'packs'):
        for path in (tmp_path / directory).iterdir():
            assert marker not in path.read_text(encoding='utf-8')


def test_upload_derived_history_and_pack_exclude_raw_file_and_private_columns(monkeypatch, tmp_path):
    import io

    from app import app
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    monkeypatch.setattr('app._UPLOAD_DIR', tmp_path / 'uploads')
    client = app.test_client()
    raw = '债券简称,收盘到期收益率(%),收盘净价(元),private-column-marker\n样本债,2,100,secret-cell\n'.encode()
    upload = client.post('/api/import', data={'file': (io.BytesIO(raw), 'private.csv')})
    assert upload.status_code == 200
    result = client.post('/api/agent/query', json={'question': '概览当前市场', 'data_mode': 'static', 'import_id': upload.get_json()['import_id']}).get_json()
    for directory in ('history', 'packs'):
        for path in (tmp_path / directory).iterdir():
            content = path.read_bytes()
            assert b'secret-cell' not in content
            assert b'private.csv' not in content
            assert b'private-column-marker' not in content
            assert raw not in content
    assert not list((tmp_path / 'uploads').iterdir())
    assert result['replay_id']


def test_auto_and_import_projection_survive_cache_clear(monkeypatch, tmp_path):
    import pandas as pd

    from app import app
    from bond_agent.agent import BondAnalystAgent
    from bond_agent.importer import describe_import_source, prepare_import_frame
    from bond_agent.replay_store import load_replay
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    monkeypatch.setattr('bond_agent.data_loader.load_live_snapshot', lambda **kw: (_ for _ in ()).throw(FileNotFoundError()))
    def fail_live():
        raise TimeoutError('offline')
    auto = BondAnalystAgent(data_mode='auto', live_fetcher=fail_live).answer('概览当前市场')
    assert load_replay(auto['replay_id'])['result']['data_source']['runtime_mode'] == 'static_fallback'
    frame, report = prepare_import_frame(pd.DataFrame({'债券简称':['private name'], '收益率':[2], '净价':[100]}), file_name='private.csv')
    source = describe_import_source(frame, report)
    imported = BondAnalystAgent(data_mode='static').answer('私人问题', data_frame=frame, data_source=source)
    record = load_replay(imported['replay_id'])
    assert record['result']['data_source']['runtime_mode'] == 'import'
    assert 'private name' not in str(record)
    assert '私人问题' not in str(record)
    assert app.test_client().get('/replay/' + imported['replay_id']).status_code == 200


def test_three_modes_recover_in_fresh_process(monkeypatch, tmp_path):
    import json
    import os
    import subprocess
    import sys

    import pandas as pd

    from bond_agent.agent import BondAnalystAgent
    from bond_agent.importer import describe_import_source, prepare_import_frame
    from bond_agent.replay_store import load_replay

    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    monkeypatch.setattr('bond_agent.data_loader.load_live_snapshot', lambda **kw: (_ for _ in ()).throw(FileNotFoundError()))
    def offline():
        raise TimeoutError('offline')
    frame, report = prepare_import_frame(pd.DataFrame({'债券简称': ['private'], '收益率': [2.13], '净价': [100]}), file_name='private.csv')
    runs = [BondAnalystAgent(data_mode='static').answer('概览当前市场'),
            BondAnalystAgent(data_mode='auto', live_fetcher=offline).answer('概览当前市场'),
            BondAnalystAgent(data_mode='static').answer('概览当前市场', data_frame=frame, data_source=describe_import_source(frame, report))]
    ids = [r['replay_id'] for r in runs]
    expected = [load_replay(rid)['result'] for rid in ids]
    code = "import json,sys; from bond_agent.replay_store import load_replay; print(json.dumps([load_replay(i)['result'] for i in sys.argv[1:]], ensure_ascii=True))"
    completed = subprocess.run([sys.executable, '-c', code, *ids], env=os.environ.copy(), capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout) == expected
    assert [r['data_source']['runtime_mode'] for r in expected] == ['static_sample', 'static_fallback', 'import']


def test_auto_import_history_through_restarted_http_service(monkeypatch, tmp_path):
    import json
    import os
    import subprocess
    import sys
    import urllib.request

    import pandas as pd

    from bond_agent.agent import BondAnalystAgent
    from bond_agent.importer import describe_import_source, prepare_import_frame
    from bond_agent.replay_store import load_replay

    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    monkeypatch.setattr('bond_agent.data_loader.load_live_snapshot', lambda **kw: (_ for _ in ()).throw(FileNotFoundError()))
    def offline():
        raise TimeoutError('offline')
    auto = BondAnalystAgent(data_mode='auto', live_fetcher=offline).answer('概览当前市场')
    frame, report = prepare_import_frame(pd.DataFrame({'债券简称': ['PRIVATE_TENANT_BRAVO'], '收益率': [2.13], '净价': [100]}), file_name='private.csv')
    imported = BondAnalystAgent(data_mode='static').answer('概览当前市场', data_frame=frame, data_source=describe_import_source(frame, report))
    records = [load_replay(r['replay_id']) for r in (auto, imported)]
    code = "from werkzeug.serving import make_server; from app import app; s=make_server('127.0.0.1',0,app); print(s.server_port,flush=True); s.serve_forever()"
    for _ in range(2):
        process = subprocess.Popen([sys.executable, '-u', '-c', code], env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            port = int(process.stdout.readline())
            for record in records:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/replay/{record["id"]}?lang=en', timeout=10) as response:
                    html = response.read().decode('utf-8')
                assert 'Saved historical result' in html
                assert 'PRIVATE_TENANT_BRAVO' not in html
                if record['result']['data_source']['runtime_mode'] == 'import':
                    assert '2.13' in html
            assert '2.13' in json.dumps(records[1]['result'], ensure_ascii=False)
        finally:
            process.terminate()
            process.communicate(timeout=10)


def test_pack_failure_does_not_advertise_missing_pack(monkeypatch, tmp_path):
    from bond_agent.agent import BondAnalystAgent
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    import bond_agent.evidence_pack as pack
    original = pack._atomic_write
    def fail_json(path, text):
        if path.suffix == '.json':
            raise OSError('simulated write failure')
        original(path, text)
    monkeypatch.setattr(pack, '_atomic_write', fail_json)
    result = BondAnalystAgent(data_mode='static').answer('概览当前市场')
    assert result['replay_id']
    assert result['evidence_pack_id'] is None
    assert not list((tmp_path / 'packs').glob('*.html'))


def test_oversize_and_atomic_write_failure_leave_no_history(monkeypatch, tmp_path):
    import bond_agent.replay_store as store

    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path))
    monkeypatch.setattr(store, '_MAX_RECORD_BYTES', 256)
    assert save_replay({'question': 'x' * 300, 'final_answer': 'answer', 'data_source': {}}) is None
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(store, '_MAX_RECORD_BYTES', 2 * 1024 * 1024)
    def fail_replace(*args):
        raise OSError('simulated disk error')
    monkeypatch.setattr(store.os, 'replace', fail_replace)
    assert save_replay({'question': 'q', 'final_answer': 'answer', 'data_source': {}}) is None
    assert list(tmp_path.iterdir()) == []


def test_concurrent_history_writes_are_readable(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from bond_agent.replay_store import load_replay
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda n: save_replay({'question': f'q{n}', 'final_answer': 'answer', 'data_source': {}}), range(40)))
    assert len({r['id'] for r in records}) == 40
    assert all(load_replay(r['id'])['result']['final_answer'] == 'answer' for r in records)


def test_missing_pack_not_linked_on_historical_result(monkeypatch, tmp_path):
    from app import app
    from bond_agent.agent import BondAnalystAgent
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_ENABLED', 'false')
    result = BondAnalystAgent(data_mode='static').answer('概览当前市场')
    response = app.test_client().get(f"/replay/{result['replay_id']}")
    assert response.status_code == 200
    assert b'data-ai-button data-result-id="None"' not in response.data
    assert result['evidence_pack_id'] is None


def test_missing_one_pack_never_advertised(monkeypatch, tmp_path):
    from app import app
    from bond_agent.agent import BondAnalystAgent

    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    result = BondAnalystAgent(data_mode='static').answer('概览当前市场')
    rid = result['replay_id']
    (tmp_path / 'packs' / f'{rid}.html').unlink()
    client = app.test_client()
    assert client.get(f'/packs/{rid}.html').status_code == 404
    page = client.get(f'/replay/{rid}').get_data(as_text=True)
    assert f'/packs/{rid}.html' not in page
    assert f'/packs/{rid}.json' in page


def test_disabled_history_preserves_existing_files_and_pack_access(monkeypatch, tmp_path):
    from app import app
    from bond_agent.replay_store import load_replay
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path / 'history'))
    monkeypatch.setenv('BOND_EVIDENCE_PACK_DIR', str(tmp_path / 'packs'))
    record = save_replay({'question': 'q', 'final_answer': 'ok', 'data_source': {}})
    pack = tmp_path / 'packs'
    pack.mkdir()
    (pack / 'independent.json').write_text('{}')
    monkeypatch.setenv('BOND_REPLAY_ENABLED', 'false')
    assert load_replay(record['id']) is None and list_replays() == []
    assert save_replay({'question': 'new'}) is None
    client = app.test_client()
    assert client.get('/replay').status_code == 404
    assert client.get('/replay/' + record['id']).status_code == 404
    assert client.get('/packs/independent.json').status_code == 200
    monkeypatch.setenv('BOND_REPLAY_ENABLED', 'true')
    assert load_replay(record['id']) is not None


def test_keyset_pagination_search_and_read_bound(monkeypatch, tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    import bond_agent.replay_store as store
    from app import app
    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path))
    for n in range(45):
        rid = (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=n)).strftime('%Y%m%d-%H%M%S') + '-12345678'
        (tmp_path / f'{rid}.json').write_text(json.dumps({'id': rid, 'created_at': '2025-01-01T00:00:00Z', 'question': 'needle' if n == 2 else 'other', 'data_source': {'runtime_mode': 'import'}, 'llm': {'status': 'disabled'}}))
    original = store.load_replay
    calls = []
    def counted(rid):
        calls.append(rid)
        return original(rid)
    monkeypatch.setattr(store, 'load_replay', counted)
    client = app.test_client()
    first = client.get('/replay')
    assert first.status_code == 200 and first.data.count(b'data-replay-row ') == 20
    assert len(calls) == 21
    cursor = '20250101-000025-12345678'
    calls.clear()
    second = client.get('/replay', query_string={'cursor': cursor})
    assert second.data.count(b'data-replay-row ') == 20 and len(calls) == 21
    search = client.get('/replay?q=needle&source=import&status=disabled')
    assert search.data.count(b'data-replay-row ') == 1 and b'20250101-000002-12345678' in search.data
    assert client.get('/replay?cursor=../bad').status_code == 400
    assert client.get('/replay?order=bad').status_code == 400
    rows, prev, nxt = store.page_replays(cursor='20250101-000005-12345678')
    assert len(rows) == 5 and prev and not nxt
    rows, prev, nxt = store.page_replays(cursor='20250101-000025-12345678', direction='prev')
    assert len(rows) == 19 and not prev and nxt
    rows, prev, nxt = store.page_replays(order='asc')
    assert rows[0]['id'] == '20250101-000000-12345678' and not prev and nxt
    assert store.page_replays(start='2025-01-02')[0] == []


def test_history_status_filters_and_severity_labels_are_bilingual(monkeypatch, tmp_path):
    import json

    from app import app

    monkeypatch.setenv('BOND_REPLAY_DIR', str(tmp_path))
    for index, status in enumerate(('unavailable', 'guardrail_failed', 'blocked')):
        rid = f'20250101-00000{index}-12345678'
        record = {'id': rid, 'question': f'question-{status}', 'data_source': {},
                  'llm': {'status': status}, 'stress_view': {'severity': 'high'}}
        (tmp_path / f'{rid}.json').write_text(json.dumps(record), encoding='utf-8')
    client = app.test_client()
    for status, zh, en in [('unavailable', '不可用', 'Unavailable'),
                            ('guardrail_failed', '护栏未通过', 'Guardrail failed'),
                            ('blocked', '已拦截', 'Blocked')]:
        html = client.get('/replay', query_string={'status': status, 'lang': 'zh'}).get_data(as_text=True)
        assert html.count('data-replay-row ') == 1
        assert f'value="{status}" selected' in html
        assert f'data-lang-zh="{zh}" data-lang-en="{en}"' in html
        assert '· <span data-lang-zh="高" data-lang-en="High">高</span>' in html
        assert '· high</p>' not in html
        english = client.get('/replay', query_string={'status': status, 'lang': 'en'}).get_data(as_text=True)
        assert 'data-lang-zh="高" data-lang-en="High"' in english


def test_history_page_can_read_more_than_default_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("BOND_REPLAY_DIR", str(tmp_path))
    for index in range(35):
        save_replay({"question": f"question {index}"})
    assert len(list_replays()) == 30
    assert len(list_replays(limit=None)) == 35
    from app import app

    response = app.test_client().get('/replay?lang=en')
    assert response.status_code == 200
    assert response.get_data(as_text=True).count('data-replay-row ') == 20

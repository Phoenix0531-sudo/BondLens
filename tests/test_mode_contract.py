import time

import pandas as pd
import pytest

from bond_agent.data_loader import resolve_bond_data


def test_live_failure_never_uses_local_fallback(tmp_path, monkeypatch):
    def fail():
        raise TimeoutError('provider down')

    def forbidden(*args, **kwargs):
        pytest.fail('live attempted local fallback')

    monkeypatch.setattr('bond_agent.data_loader.load_live_snapshot', forbidden)
    monkeypatch.setattr('bond_agent.data_loader.load_bond_data', forbidden)
    with pytest.raises(Exception, match='实时数据不可用'):
        resolve_bond_data(mode='live', live_fetcher=fail, live_cache_path=tmp_path / 'cache.csv')


def test_navigation_preserves_explicit_mode():
    from app import app

    response = app.test_client().get('/market?data_mode=static&lang=en')
    assert b'/screen?data_mode=static' in response.data


@pytest.mark.parametrize('raw', [
    pd.DataFrame(),
    pd.DataFrame({'债券简称': ['x']}),
    pd.DataFrame({'债券简称': ['x'], '成交净价': ['bad'], '最新收益率': ['bad'], '加权收益率': [1], '交易量': [1]}),
])
def test_live_rejects_invalid_provider_payload(raw, tmp_path):
    with pytest.raises(Exception, match='实时数据不可用'):
        resolve_bond_data(mode='live', live_fetcher=lambda: raw, live_cache_path=tmp_path / 'cache.csv')


def test_live_filters_bad_rows_and_sanitizes_optional_numbers(tmp_path):
    from bond_agent.data_loader import LIVE_CHANGE_BP, PRICE, VOLUME, WEIGHTED_YIELD, YIELD
    raw = pd.DataFrame({
        '债券简称': ['valid', 'badprice', 'badyield', '', 'negative'],
        '成交净价': [100, float('inf'), 101, 102, 99],
        '最新收益率': [2, 3, float('-inf'), 4, -1.25],
        '加权收益率': [float('inf'), 3, 3, 3, 'bad'],
        '交易量': [float('-inf'), 1, 1, 1, -4],
        '涨跌': ['1e999', 1, 1, 1, 0],
    })
    frame, source = resolve_bond_data(mode='live', live_fetcher=lambda: raw, live_cache_path=tmp_path / 'cache.csv')
    assert frame['债券简称'].tolist() == ['valid', 'negative']
    assert frame[YIELD].tolist() == [2, -1.25]
    assert frame[PRICE].tolist() == [100, 99]
    assert frame.iloc[0][[WEIGHTED_YIELD, VOLUME, LIVE_CHANGE_BP]].isna().all()
    assert pd.isna(frame.iloc[1][VOLUME])
    assert source['row_count'] == 2


def test_large_finite_yields_are_not_silently_clamped(tmp_path):
    raw = pd.DataFrame({'债券简称': ['extreme'], '成交净价': [100], '最新收益率': [1000], '加权收益率': [1000], '交易量': [1]})
    frame, _ = resolve_bond_data(mode='live', live_fetcher=lambda: raw, live_cache_path=tmp_path / 'cache.csv')
    assert frame['收盘到期收益率(%)'].tolist() == [1000]


def test_overflowing_maturity_is_not_accepted_as_finite(tmp_path):
    raw = pd.DataFrame({'债券简称': ['x'], '成交净价': [100], '最新收益率': [2], '加权收益率': [2], '交易量': [1], '待偿期': ['9' * 400 + 'Y']})
    frame, _ = resolve_bond_data(mode='live', live_fetcher=lambda: raw, live_cache_path=tmp_path / 'cache.csv')
    assert pd.isna(frame['待偿期(年)'].iloc[0])


def test_live_only_nonfinite_rows_are_unavailable(tmp_path):
    raw = pd.DataFrame({'债券简称': ['bad'], '成交净价': [float('inf')], '最新收益率': [1], '加权收益率': [1], '交易量': [1]})
    with pytest.raises(Exception, match='实时数据不可用'):
        resolve_bond_data(mode='live', live_fetcher=lambda: raw, live_cache_path=tmp_path / 'cache.csv')


def test_strict_live_does_not_read_static_master(tmp_path, monkeypatch):
    raw = pd.DataFrame({'债券简称': ['x'], '成交净价': [100], '最新收益率': [2], '加权收益率': [2], '交易量': [1]})
    monkeypatch.setattr('bond_agent.data_loader.load_bond_data', lambda *a, **k: pytest.fail('static accessed'))
    frame, source = resolve_bond_data(mode='live', live_fetcher=lambda: raw, live_cache_path=tmp_path / 'cache.csv')
    assert source['runtime_mode'] == 'live'
    assert frame['待偿期(年)'].isna().all()


def test_live_timeout_returns_without_waiting_for_worker(tmp_path, monkeypatch):
    monkeypatch.setenv('BOND_LIVE_FETCH_TIMEOUT_SECONDS', '0.05')
    def slow():
        time.sleep(0.8)
    start = time.monotonic()
    with pytest.raises(Exception, match='实时数据不可用'):
        resolve_bond_data(mode='live', live_fetcher=slow, live_cache_path=tmp_path / 'cache.csv')
    assert time.monotonic() - start < 0.6


def test_static_never_calls_live_fetcher():
    def forbidden():
        pytest.fail('static called live')
    frame, source = resolve_bond_data(mode='static', live_fetcher=forbidden)
    assert len(frame) > 0
    assert source['runtime_mode'] == 'static_sample'
    assert source['sample_date'] == '2024-03-22'


def test_import_id_overrides_static_and_expired_never_falls_back(monkeypatch):
    import app as app_module
    from bond_agent.data_loader import load_bond_data
    frame = load_bond_data().head(3).copy()
    token = app_module._store_import(frame, {'runtime_mode': 'import', 'source_id': 'upload'})
    def forbidden(*args, **kwargs):
        pytest.fail('import resolved another source')
    monkeypatch.setattr(app_module, 'resolve_bond_data', forbidden)
    client = app_module.app.test_client()
    ok = client.get('/api/market/overview', query_string={'data_mode': 'static', 'import_id': token})
    assert ok.status_code == 200
    assert ok.get_json()['data_source']['runtime_mode'] == 'import'
    expired = client.get('/api/market/overview?data_mode=static&import_id=expired')
    assert expired.status_code == 400
    assert '过期' in expired.get_json()['error']
    export = client.get('/api/maturity/unmatched?format=json&data_mode=static&import_id=expired')
    assert export.status_code == 400
    assert '过期' in export.get_json()['error']


def test_live_api_error_is_safe(monkeypatch):
    import app as app_module
    def fail(**kwargs):
        from bond_agent.data_loader import LiveDataUnavailable
        raise LiveDataUnavailable('实时数据不可用，请重试。 / Live data unavailable; please retry.')
    monkeypatch.setattr(app_module, 'resolve_bond_data', fail)
    monkeypatch.setattr('bond_agent.agent.resolve_bond_data', fail)
    client = app_module.app.test_client()
    for path in ['/api/market/overview', '/api/data/source', '/api/bonds/search', '/api/bond/detail?code=230026']:
        separator = '&' if '?' in path else '?'
        response = client.get(path + separator + 'data_mode=live')
        assert response.status_code == 502, path
        assert '实时数据不可用' in response.get_json()['error']
    response = client.post('/api/agent/query', json={'question': '市场概览', 'data_mode': 'live'})
    assert response.status_code == 502
    assert '实时数据不可用' in response.get_json()['error']
    stream = client.post('/api/agent/stream', json={'question': '市场概览', 'data_mode': 'live'})
    assert stream.status_code == 200
    assert '实时数据不可用' in stream.get_data(as_text=True)
    assert 'event: final' not in stream.get_data(as_text=True)

"""Phase 2 workbench tests: pages, read-only APIs, import honesty, error paths."""

import io
import json

from app import app


def _upload(client, csv_text, filename="sample.csv", overrides=None):
    data = {"file": (io.BytesIO(csv_text.encode("utf-8-sig")), filename)}
    if overrides:
        data["column_overrides"] = json.dumps(overrides, ensure_ascii=False)
    return client.post("/api/import", data=data, content_type="multipart/form-data")


def test_home_redirects_to_market_page():
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 302
    assert "/market" in response.headers["Location"]


def test_workbench_pages_render_with_shared_source_bar():
    client = app.test_client()

    market = client.get("/market?data_mode=static")
    assert market.status_code == 200
    html = market.get_data(as_text=True)
    assert "市场观察" in html
    assert "data-source-bar" in html
    assert "workbench.js" in html
    assert "market.js" in html

    screen = client.get("/screen?data_mode=static")
    assert screen.status_code == 200
    assert "债券筛选" in screen.get_data(as_text=True)
    assert "screen.js" in screen.get_data(as_text=True)

    bond_index = client.get("/bond?data_mode=static")
    assert bond_index.status_code == 200
    assert "单券研究" in bond_index.get_data(as_text=True)
    assert "bond.js" in bond_index.get_data(as_text=True)


def test_bond_page_passes_key_prefill():
    client = app.test_client()

    response = client.get("/bond/23%E9%99%84%E6%81%AF%E5%9B%BD%E5%80%BA26?data_mode=static")

    assert response.status_code == 200
    assert 'data-bond-key="23附息国债26"' in response.get_data(as_text=True)


def test_topbar_has_four_primary_entries():
    client = app.test_client()

    html = client.get("/market?data_mode=static").get_data(as_text=True)

    assert 'href="/market"' in html
    assert 'href="/screen?data_mode=static' in html
    assert 'href="/bond"' in html
    assert 'href="/replay"' in html
    assert 'href="/agent"' in html  # secondary entry stays available


def test_market_overview_api_reports_source_and_segments():
    client = app.test_client()

    response = client.get("/api/market/overview?data_mode=static")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["sample_count"] == 3365
    source = payload["data_source"]
    assert source["runtime_mode"] == "static_sample"
    assert source["sample_date"] == "2024-03-22"
    assert source["mixed_timepoints"] is False
    assert source["row_count"] > 0
    assert payload["segments"]["by_bond_type"]
    assert payload["segments"]["by_maturity_bucket"]
    assert payload["yield_summary"]["count"] > 0
    assert payload["data_quality"]["level"] in {"high", "medium", "low"}
    # Bare NaN is invalid JSON in browsers; the payload must not contain it.
    assert "NaN" not in response.get_data(as_text=True)


def test_bonds_search_filters_sort_and_pagination():
    client = app.test_client()

    response = client.get("/api/bonds/search?data_mode=static&name=国债&sort=maturity&direction=asc&page=2&page_size=10")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["match_count"] == 116
    assert payload["page"] == 2
    assert payload["page_count"] == 12
    assert len(payload["records"]) == 10
    # Records on page 2 (sorted ascending by maturity) start past the first 10.
    assert payload["records"][0]["债券简称"]

    typed = client.get("/api/bonds/search?data_mode=static&bond_type=国债&min_yield=1&max_yield=2.5&min_volume=0").get_json()
    assert 0 < typed["match_count"] < 116
    assert all(record["type_value"] == "国债" for record in typed["records"])


def test_bonds_search_rejects_bad_params():
    client = app.test_client()

    bad_sort = client.get("/api/bonds/search?data_mode=static&sort=nonsense")
    assert bad_sort.status_code == 400

    bad_direction = client.get("/api/bonds/search?data_mode=static&direction=sideways")
    assert bad_direction.status_code == 400

    bad_float = client.get("/api/bonds/search?data_mode=static&min_yield=abc")
    assert bad_float.status_code == 400


def test_bonds_search_empty_result_is_honest_zero():
    client = app.test_client()

    response = client.get("/api/bonds/search?data_mode=static&name=不存在的债券ZZZ")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["match_count"] == 0
    assert payload["records"] == []
    assert payload["page_count"] == 1


def test_bonds_search_advertises_available_filters():
    client = app.test_client()

    payload = client.get("/api/bonds/search?data_mode=static&page_size=1").get_json()

    available = payload["available_filters"]
    # The static sample has no bond-code column: the UI must know.
    assert available["code"] is False
    assert available["yield"] is True
    assert available["maturity"] is True
    assert {entry["bond_type"] for entry in available["bond_types"]} >= {"国债", "信用债"}


def test_bond_detail_by_name_with_peer_comparison():
    client = app.test_client()

    response = client.get("/api/bond/detail?key=23附息国债26&data_mode=static")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["target"]["name"] == "23附息国债26"
    assert payload["target"]["yield_value"] > 0
    peer = payload["comparison"]["peer_comparison"]
    assert peer["peer_count"] == 24
    assert peer["peer_yield_median"] > 0
    # Credit honesty: no rating inference from quote data.
    assert payload["comparison"]["credit_context"]["available"] is False


def test_bond_detail_unknown_key_is_404_and_missing_key_400():
    client = app.test_client()

    missing = client.get("/api/bond/detail?key=不存在999&data_mode=static")
    assert missing.status_code == 404
    assert "未找到" in missing.get_json()["error"]

    no_key = client.get("/api/bond/detail?data_mode=static")
    assert no_key.status_code == 400


def test_data_source_api_reports_identity():
    client = app.test_client()

    response = client.get("/api/data/source?data_mode=static")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["runtime_mode"] == "static_sample"
    assert payload["row_count"] == 3365
    assert "债券简称" in payload["available_columns"]


def test_stale_import_id_is_rejected_by_workbench_apis():
    client = app.test_client()

    for path in ["/api/market/overview", "/api/bonds/search", "/api/bond/detail?key=A", "/api/data/source"]:
        response = client.get(f"{path}&import_id=deadbeef" if "?" in path else f"{path}?import_id=deadbeef")
        assert response.status_code == 400, path


def test_forbidden_caliber_overrides_rejected_on_import():
    client = app.test_client()
    csv_text = "债券简称,全价\n测试债,101.5\n"

    price = _upload(client, csv_text, overrides={"收盘净价(元)": "全价"})
    assert price.status_code == 400
    message = price.get_json()["error"]
    assert "全价" in message
    assert "应计利息" in message
    assert "accrued interest" in message

    volume = _upload(
        client,
        "债券简称,成交金额\n测试债,5000\n",
        overrides={"交易量(亿元)": "成交金额"},
    )
    assert volume.status_code == 400
    assert "成交金额" in volume.get_json()["error"]


def test_import_flow_feeds_workbench_apis_and_flags_mixed_timepoints():
    client = app.test_client()

    mixed = _upload(
        client,
        "债券简称,收盘到期收益率(%),待偿期(年),数据日期\n"
        "甲债,2.5,3.0,2024-03-20\n"
        "乙债,2.6,5.0,2024-03-22\n",
        filename="mixed.csv",
    )
    assert mixed.status_code == 200
    mixed_payload = mixed.get_json()
    assert mixed_payload["report"]["mixed_timepoints"] is True
    assert mixed_payload["report"]["distinct_date_count"] == 2
    assert mixed_payload["report"]["quote_date_range"]
    import_id = mixed_payload["import_id"]

    overview = client.get(f"/api/market/overview?import_id={import_id}").get_json()
    assert overview["data_source"]["mixed_timepoints"] is True
    assert overview["data_source"]["row_count"] == 2

    # A yield-less import disables the yield filter with an explicit marker, never crashes.
    yieldless = _upload(client, "债券简称,收盘净价(元)\n甲债,100.2\n", filename="yieldless.csv")
    assert yieldless.status_code == 200
    yieldless_id = yieldless.get_json()["import_id"]
    search = client.get(f"/api/bonds/search?import_id={yieldless_id}&min_yield=1").get_json()
    assert search["match_count"] == 0
    assert "yield_column_missing" in search["matched_by"]
    assert any("yield_column_missing" in note or "缺少收益率列" in note for note in search["notes"])

    detail = client.get(f"/api/bond/detail?key=甲债&import_id={yieldless_id}").get_json()
    assert detail["target"]["name"] == "甲债"
    assert detail["comparison"]["peer_comparison"]["peer_count"] == 0


def test_unknown_column_override_still_maps_and_feeds_detail():
    client = app.test_client()

    upload = _upload(
        client,
        "债券名称,收益率(%),剩余年限\n丙债,3.1,4.5\n",
        filename="renamed.csv",
        overrides={"债券简称": "债券名称", "收盘到期收益率(%)": "收益率(%)", "待偿期": "剩余年限"},
    )
    assert upload.status_code == 200
    mapped = upload.get_json()["report"]["mapped_columns"]
    assert mapped["债券简称"] == "债券名称"
    assert mapped["收盘到期收益率(%)"] == "收益率(%)"
    assert mapped["待偿期"] == "剩余年限"
    import_id = upload.get_json()["import_id"]

    detail = client.get(f"/api/bond/detail?key=丙债&import_id={import_id}").get_json()
    assert detail["target"]["name"] == "丙债"
    assert abs(detail["target"]["yield_value"] - 3.1) < 1e-9

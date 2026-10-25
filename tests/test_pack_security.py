"""Evidence downloads must stay within server-configured roots."""

import pytest

import app as web


@pytest.fixture
def pack_roots(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("BOND_EVIDENCE_PACK_DIR", str(allowed))
    monkeypatch.setattr(web, "DEFAULT_PACK_DIR", allowed)
    monkeypatch.setattr(web, "DEMO_PACK_DIR", allowed)
    return allowed, private


@pytest.mark.parametrize("extension", ["json", "html"])
def test_client_cannot_select_download_directory(pack_roots, extension):
    _, private = pack_roots
    (private / f"secret.{extension}").write_text("private content", encoding="utf-8")
    response = web.app.test_client().get(
        f"/packs/secret.{extension}", query_string={"dir": str(private)}
    )
    assert response.status_code == 404
    assert b"private content" not in response.data


def test_configured_pack_root_still_works(pack_roots):
    allowed, _ = pack_roots
    (allowed / "example.json").write_text('{"ok": true}', encoding="utf-8")
    response = web.app.test_client().get("/packs/example.json")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_symlink_cannot_escape_download_root(pack_roots):
    allowed, private = pack_roots
    target = private / "secret.json"
    target.write_text('{"private": true}', encoding="utf-8")
    try:
        (allowed / "secret.json").symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit symlink creation")
    assert web.app.test_client().get("/packs/secret.json").status_code == 404

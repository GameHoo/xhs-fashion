import io
import subprocess
from http.client import HTTPResponse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import xhs_cli.runtime as runtime
from xhs_cli.runtime import (
    AUTH_HINTS,
    CLIError,
    AuthRequiredError,
    CommandResult,
    build_note_url,
    download_image,
    extract_json_blob,
    infer_extension,
    looks_like_auth_error,
    parse_qr_response,
    sanitize_url,
    _filter_note_results,
    _paginate,
)


# ---------------------------------------------------------------------------
# Existing tests
# ---------------------------------------------------------------------------


def test_extract_json_blob():
    payload = extract_json_blob("prefix\n{\n  \"ok\": true,\n  \"count\": 2\n}\n")
    assert payload == {"ok": True, "count": 2}


def test_parse_qr_response():
    text, expires_at, image_b64 = parse_qr_response(
        """{
  content: [
    { type: 'text', text: '请用小红书 App 在 2026-03-07 16:53:24 前扫码登录 👇' },
    { type: 'image', data: 'YWJj', mimeType: 'image/png' }
  ]
}"""
    )
    assert "扫码登录" in text
    assert expires_at == "2026-03-07 16:53:24"
    assert image_b64 == "YWJj"


def test_search_raw_non_auth_failure_raises_cli_error(monkeypatch):
    def fake_mcporter_call(*args, **kwargs):
        return CommandResult(
            args=["mcporter", "call"],
            returncode=1,
            stdout="",
            stderr="connection reset by peer",
        )

    monkeypatch.setattr(runtime, "mcporter_call", fake_mcporter_call)

    with pytest.raises(CLIError) as exc_info:
        runtime._search_raw("男士 宽松 穿搭")

    assert exc_info.value.payload["status"] == "error"
    assert exc_info.value.payload["message"] == "Search call failed"
    assert exc_info.value.payload["returncode"] == 1


def test_search_raw_auth_failure_raises_auth_required(monkeypatch):
    def fake_mcporter_call(*args, **kwargs):
        return CommandResult(
            args=["mcporter", "call"],
            returncode=1,
            stdout="",
            stderr="failed to load cookies",
        )

    monkeypatch.setattr(runtime, "mcporter_call", fake_mcporter_call)

    with pytest.raises(AuthRequiredError):
        runtime._search_raw("男士 宽松 穿搭")


def test_run_command_timeout_raises_cli_error(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["mcporter", "call"], timeout=5, output="partial", stderr="slow")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CLIError) as exc_info:
        runtime.run_command(["mcporter", "call"], timeout=5, failure_message="mcporter call failed")

    assert exc_info.value.payload["status"] == "error"
    assert exc_info.value.payload["message"] == "mcporter call failed"
    assert exc_info.value.payload["timeout_seconds"] == 5
    assert exc_info.value.payload["stdout"] == "partial"
    assert exc_info.value.payload["stderr"] == "slow"


def test_login_status_payload_uses_local_cookie_state(monkeypatch, tmp_path):
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(runtime, "COOKIE_FILE", cookie_file)
    monkeypatch.setattr(runtime, "pending_login_state", lambda: (False, "", tmp_path / "login-qrcode.png"))
    monkeypatch.setattr(runtime, "probe_logged_in", lambda: pytest.fail("login_status_payload should not probe auth"))

    payload = runtime.login_status_payload()

    assert payload["status"] == "logged_in"
    assert payload["cookie_path"] == str(cookie_file)
    assert payload["auth_validation"] == "not_run"


def test_run_command_os_error_raises_cli_error(monkeypatch):
    def fake_run(*args, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CLIError) as exc_info:
        runtime.run_command(["mcporter", "call"], failure_message="mcporter call failed")

    assert exc_info.value.payload["status"] == "error"
    assert exc_info.value.payload["message"] == "mcporter call failed"
    assert exc_info.value.payload["error"] == "exec format error"


# ---------------------------------------------------------------------------
# New tests: sanitize_url
# ---------------------------------------------------------------------------


def test_sanitize_url_converts_http_to_https():
    assert sanitize_url("http://example.com/img.jpg") == "https://example.com/img.jpg"


def test_sanitize_url_keeps_https():
    assert sanitize_url("https://example.com/img.jpg") == "https://example.com/img.jpg"


def test_sanitize_url_keeps_other_schemes():
    assert sanitize_url("ftp://example.com/file") == "ftp://example.com/file"


# ---------------------------------------------------------------------------
# New tests: infer_extension
# ---------------------------------------------------------------------------


def test_infer_extension_from_content_type():
    assert infer_extension("image/jpeg", "https://example.com/img") == ".jpg"
    assert infer_extension("image/png", "https://example.com/img") == ".png"
    assert infer_extension("image/webp", "https://example.com/img") == ".webp"


def test_infer_extension_from_url_fallback():
    assert infer_extension(None, "https://example.com/photo.jpeg?w=100") == ".jpg"
    assert infer_extension("", "https://example.com/photo.png") == ".png"
    assert infer_extension(None, "https://example.com/photo.gif") == ".gif"


def test_infer_extension_unknown():
    assert infer_extension(None, "https://example.com/blob") == ".img"


# ---------------------------------------------------------------------------
# New tests: looks_like_auth_error
# ---------------------------------------------------------------------------


def test_looks_like_auth_error_positive():
    assert looks_like_auth_error("Error: 未登录") is True
    assert looks_like_auth_error("failed to load cookies from disk") is True


def test_looks_like_auth_error_negative():
    assert looks_like_auth_error("network timeout") is False
    assert looks_like_auth_error("connection reset") is False


# ---------------------------------------------------------------------------
# New tests: build_note_url
# ---------------------------------------------------------------------------


def test_build_note_url():
    url = build_note_url("abc123", "token456")
    assert "abc123" in url
    assert "token456" in url
    assert url.startswith("https://www.xiaohongshu.com/explore/")


# ---------------------------------------------------------------------------
# New tests: _filter_note_results
# ---------------------------------------------------------------------------


def test_filter_note_results():
    feeds = [
        {"modelType": "note", "id": "1"},
        {"modelType": "hot_query", "id": "2"},
        {"modelType": "note", "id": "3"},
    ]
    result = _filter_note_results(feeds)
    assert len(result) == 2
    assert all(f["modelType"] == "note" for f in result)


def test_filter_note_results_empty():
    assert _filter_note_results([]) == []


# ---------------------------------------------------------------------------
# New tests: _paginate
# ---------------------------------------------------------------------------


def test_paginate_first_page():
    items = [{"id": i} for i in range(10)]
    result = _paginate(items, page=1, page_size=3)
    assert len(result) == 3
    assert result[0]["id"] == 0


def test_paginate_second_page():
    items = [{"id": i} for i in range(10)]
    result = _paginate(items, page=2, page_size=3)
    assert len(result) == 3
    assert result[0]["id"] == 3


def test_paginate_beyond_range():
    items = [{"id": i} for i in range(3)]
    result = _paginate(items, page=5, page_size=3)
    assert result == []


def test_paginate_invalid_page():
    with pytest.raises(CLIError):
        _paginate([], page=0, page_size=5)


def test_paginate_invalid_page_size():
    with pytest.raises(CLIError):
        _paginate([], page=1, page_size=0)


# ---------------------------------------------------------------------------
# New tests: download_image
# ---------------------------------------------------------------------------


def _make_fake_response(content: bytes, content_type: str = "image/jpeg", content_length: str | None = None):
    """Create a mock HTTP response for urlopen."""
    mock_response = MagicMock()
    mock_response.read = MagicMock(return_value=content)
    mock_response.headers = MagicMock()
    mock_response.headers.get = lambda key, default=None: {
        "Content-Type": content_type,
        "Content-Length": content_length,
    }.get(key, default)
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def test_download_image_success(tmp_path):
    fake_content = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG header
    mock_resp = _make_fake_response(fake_content, "image/jpeg")

    with patch("xhs_cli.runtime.urlopen", return_value=mock_resp):
        dest = download_image("https://example.com/photo.jpg", tmp_path / "test_img")

    assert dest.exists()
    assert dest.suffix == ".jpg"
    assert dest.read_bytes() == fake_content


def test_download_image_png(tmp_path):
    fake_content = b"\x89PNG" + b"\x00" * 50
    mock_resp = _make_fake_response(fake_content, "image/png")

    with patch("xhs_cli.runtime.urlopen", return_value=mock_resp):
        dest = download_image("https://example.com/photo", tmp_path / "test_img")

    assert dest.suffix == ".png"


def test_download_image_rejects_oversized_content_length(tmp_path):
    mock_resp = _make_fake_response(b"", content_length="999999999")

    with patch("xhs_cli.runtime.urlopen", return_value=mock_resp):
        with pytest.raises(ValueError, match="too large"):
            download_image("https://example.com/huge.jpg", tmp_path / "img", max_bytes=1024)


def test_download_image_rejects_oversized_body(tmp_path):
    oversized = b"\x00" * 2000
    mock_resp = _make_fake_response(oversized, "image/jpeg")
    # read() returns the full oversized content
    mock_resp.read = MagicMock(return_value=oversized)

    with patch("xhs_cli.runtime.urlopen", return_value=mock_resp):
        with pytest.raises(ValueError, match="exceeds size limit"):
            download_image("https://example.com/big.jpg", tmp_path / "img", max_bytes=1024)


def test_download_image_converts_http_to_https(tmp_path):
    fake_content = b"\xff\xd8" + b"\x00" * 10
    mock_resp = _make_fake_response(fake_content, "image/jpeg")
    captured_requests = []

    def fake_urlopen(req, **kwargs):
        captured_requests.append(req.full_url)
        return mock_resp

    with patch("xhs_cli.runtime.urlopen", side_effect=fake_urlopen):
        download_image("http://example.com/photo.jpg", tmp_path / "test_img")

    assert captured_requests[0].startswith("https://")


# ---------------------------------------------------------------------------
# New tests: service_alive
# ---------------------------------------------------------------------------


def test_service_alive_returns_true_on_success():
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("xhs_cli.runtime.urlopen", return_value=mock_resp):
        assert runtime.service_alive() is True


def test_service_alive_returns_true_on_405():
    from urllib.error import HTTPError

    def fake_urlopen(*args, **kwargs):
        raise HTTPError(url="", code=405, msg="", hdrs=None, fp=None)

    with patch("xhs_cli.runtime.urlopen", side_effect=fake_urlopen):
        assert runtime.service_alive() is True


def test_service_alive_returns_false_on_connection_refused():
    from urllib.error import URLError

    def fake_urlopen(*args, **kwargs):
        raise URLError("Connection refused")

    with patch("xhs_cli.runtime.urlopen", side_effect=fake_urlopen):
        assert runtime.service_alive() is False


def test_service_alive_returns_false_on_404():
    from urllib.error import HTTPError

    def fake_urlopen(*args, **kwargs):
        raise HTTPError(url="", code=404, msg="", hdrs=None, fp=None)

    with patch("xhs_cli.runtime.urlopen", side_effect=fake_urlopen):
        assert runtime.service_alive() is False


# ---------------------------------------------------------------------------
# New tests: extract_json_blob edge cases
# ---------------------------------------------------------------------------


def test_extract_json_blob_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        extract_json_blob("")


def test_extract_json_blob_no_json_raises():
    with pytest.raises(ValueError, match="no JSON"):
        extract_json_blob("just some text without json")


def test_extract_json_blob_nested():
    payload = extract_json_blob('debug output\n{"data": {"items": [1,2]}, "ok": true}\nmore output')
    assert payload == {"data": {"items": [1, 2]}, "ok": True}


# ---------------------------------------------------------------------------
# New tests: search_images flow
# ---------------------------------------------------------------------------


def test_search_images_ok(monkeypatch, tmp_path):
    """Test the full search_images flow with mocked dependencies."""
    fake_search_result = {
        "feeds": [
            {
                "id": "feed001",
                "xsecToken": "tok1",
                "modelType": "note",
                "noteCard": {
                    "displayTitle": "Test Outfit",
                    "type": "normal",
                    "user": {"nickname": "testuser", "userId": "u1"},
                    "cover": {"urlDefault": "https://example.com/cover.jpg"},
                },
            }
        ]
    }

    monkeypatch.setattr(runtime, "ensure_service", lambda: None)
    monkeypatch.setattr(
        runtime,
        "mcporter_call",
        lambda *a, **kw: CommandResult(
            args=["mcporter"],
            returncode=0,
            stdout='result: ' + runtime.json_dump(fake_search_result),
            stderr="",
        ),
    )

    fake_img = b"\xff\xd8\xff\xe0" + b"\x00" * 50
    mock_resp = _make_fake_response(fake_img, "image/jpeg")
    monkeypatch.setattr(runtime, "urlopen", lambda *a, **kw: mock_resp)

    result = runtime.search_images(
        keyword="test",
        image_dir=tmp_path,
        page=1,
        page_size=10,
        image_mode="cover",
        fallback_to_cover=True,
        login_policy="fail",
        login_timeout=10,
    )

    assert result["status"] == "ok"
    assert result["returned"] == 1
    assert result["image_count"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Test Outfit"
    assert Path(result["download_dir"]).exists()


def test_search_images_auth_fail_policy(monkeypatch, tmp_path):
    """Test that login_policy='fail' raises AuthRequiredError."""
    monkeypatch.setattr(runtime, "ensure_service", lambda: None)
    monkeypatch.setattr(
        runtime,
        "mcporter_call",
        lambda *a, **kw: CommandResult(
            args=["mcporter"],
            returncode=1,
            stdout="",
            stderr="failed to load cookies",
        ),
    )

    with pytest.raises((AuthRequiredError, CLIError)):
        runtime.search_images(
            keyword="test",
            image_dir=tmp_path,
            page=1,
            page_size=10,
            image_mode="cover",
            fallback_to_cover=True,
            login_policy="fail",
            login_timeout=10,
        )

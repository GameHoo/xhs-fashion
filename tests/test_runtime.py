import subprocess

import pytest

import xhs_cli.runtime as runtime
from xhs_cli.runtime import CLIError, AuthRequiredError, CommandResult, extract_json_blob, parse_qr_response


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

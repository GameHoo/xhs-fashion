"""Test xhs CLI commands via CliRunner (user-facing interface).

Unit tests: run with `uv run pytest tests/test_cli.py`
Integration tests (需要真实 MCP 服务 + 手动扫码):
    uv run pytest tests/test_cli.py -m login -s
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from xhs_cli.app import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# xhs --version
# ---------------------------------------------------------------------------


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


# ---------------------------------------------------------------------------
# xhs search images
# ---------------------------------------------------------------------------


@pytest.mark.search
def test_search_images_returns_images(tmp_path):
    """集成测试: 真实调用 MCP 服务搜索图片并验证返回结果"""
    import shutil
    from xhs_cli.runtime import service_alive

    if not shutil.which("mcporter"):
        pytest.skip("mcporter 不在 PATH 中")
    if not service_alive():
        pytest.skip("xiaohongshu-mcp 服务未运行 (localhost:18060)")

    result = runner.invoke(app, [
        "search", "images",
        "--keyword", "男生 穿搭",
        "--image-dir", str(tmp_path),
        "--json",
    ])

    if result.exit_code == 10:
        pytest.skip("未登录，跳过搜索测试")

    assert result.exit_code == 0, f"搜索失败 (exit={result.exit_code}): {result.stdout}"
    out = json.loads(result.stdout)
    assert out["status"] in ("ok", "partial")
    assert out["returned"] > 0, "搜索结果为空"
    assert out["image_count"] > 0, "没有下载到图片"

    # 验证图片文件确实存在
    for item in out["items"]:
        for img_path in item["image_paths"]:
            full_path = Path(out["download_dir"]) / img_path
            assert full_path.exists(), f"图片文件不存在: {full_path}"


# ---------------------------------------------------------------------------
# xhs login status
# ---------------------------------------------------------------------------


def test_login_status_logged_in():
    payload = {
        "status": "logged_in",
        "cookie_path": "/tmp/cookies.json",
        "auth_validation": "not_run",
    }

    with patch("xhs_cli.app.login_status_payload", return_value=payload):
        result = runner.invoke(app, ["login", "status", "--json"])

    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["status"] == "logged_in"


def test_login_status_logged_out():
    payload = {"status": "logged_out"}

    with patch("xhs_cli.app.login_status_payload", return_value=payload):
        result = runner.invoke(app, ["login", "status", "--json"])

    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["status"] == "logged_out"


# ---------------------------------------------------------------------------
# xhs login start
# ---------------------------------------------------------------------------


def test_login_start_already_logged_in():
    payload = {
        "status": "logged_in",
        "auth_validation": "ok",
    }

    with patch("xhs_cli.app.start_login", return_value=payload):
        result = runner.invoke(app, ["login", "start", "--json"])

    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["status"] == "logged_in"


def test_login_start_pending():
    payload = {
        "status": "pending_login",
        "qr_image_path": "/tmp/qr.png",
        "expires_at": "2026-03-08 12:00:00",
    }

    with patch("xhs_cli.app.start_login", return_value=payload):
        result = runner.invoke(app, ["login", "start", "--json"])

    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["status"] == "pending_login"
    assert "qr_image_path" in out


# ---------------------------------------------------------------------------
# xhs login reset
# ---------------------------------------------------------------------------


def test_login_reset():
    payload = {"status": "reset_done"}

    with patch("xhs_cli.app.reset_login", return_value=payload):
        result = runner.invoke(app, ["login", "reset", "--json"])

    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["status"] == "reset_done"


# ---------------------------------------------------------------------------
# Integration: login flow (需要真实 MCP 服务 + 手动扫码)
#   uv run pytest tests/test_cli.py -m login -s
# ---------------------------------------------------------------------------


@pytest.mark.login
def test_login_flow():
    """完整登录流程: 清除 → 确认未登录 → 生成二维码 → 等待扫码 → 确认已登录"""
    import shutil
    from xhs_cli.runtime import service_alive

    if not shutil.which("mcporter"):
        pytest.skip("mcporter 不在 PATH 中")
    if not service_alive():
        pytest.skip("xiaohongshu-mcp 服务未运行 (localhost:18060)")

    # 1. 清除登录状态
    result = runner.invoke(app, ["login", "reset", "--json"])
    assert result.exit_code == 0, f"reset 失败 (exit={result.exit_code}): {result.stdout}"
    out = json.loads(result.stdout)
    assert out["status"] == "reset_done"
    print("\n[1/4] 已清除登录状态")

    # 2. 确认是未登录
    result = runner.invoke(app, ["login", "status", "--json"])
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["status"] == "logged_out", f"期望 logged_out，实际 {out['status']}"
    print("[2/4] 确认未登录")

    # 3. 生成二维码
    result = runner.invoke(app, ["login", "start", "--json"])
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["status"] == "pending_login", f"期望 pending_login，实际 {out['status']}"
    qr_path = out.get("qr_image_path", "")
    expires = out.get("expires_at", "未知")
    print(f"[3/4] 二维码已生成")
    print(f"      路径: {qr_path}")
    print(f"      过期: {expires}")
    print(f"      请用小红书 App 扫描二维码 👆")

    # 4. 轮询等待登录成功
    timeout = 120
    poll_interval = 5
    start = time.time()
    logged_in = False

    while time.time() - start < timeout:
        time.sleep(poll_interval)
        elapsed = int(time.time() - start)
        result = runner.invoke(app, ["login", "status", "--json"])
        out = json.loads(result.stdout)
        if out["status"] == "logged_in":
            logged_in = True
            print(f"[4/4] 登录成功! ({elapsed}s)")
            break
        print(f"      等待扫码... ({elapsed}s/{timeout}s)")

    assert logged_in, f"登录超时 ({timeout}s)，最后状态: {out['status']}"
